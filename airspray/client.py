"""
Asynchronous BLE Client for Air Wick Bluetooth Diffuser.
"""

import asyncio
import logging
from datetime import datetime
from types import TracebackType
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple, Type, Union
from bleak import BleakClient, BleakScanner
from bleak.backends.characteristic import BleakGATTCharacteristic
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData

from airspray.protocol import (
    SERVICE_UUID,
    WRITE_CHAR_UUID,
    NOTIFY_CHAR_UUID,
    is_recognised_device_name,
    DiffuserStatus,
    Schedule,
    encode_set_power,
    encode_set_intensity,
    encode_boost,
    encode_query_status,
    encode_query_mcu_firmware,
    encode_query_ble_firmware,
    encode_set_time,
    encode_query_schedule,
    encode_set_schedule,
    encode_delete_schedule,
    encode_clear_schedule,
    encode_set_room_and_fragrance,
    encode_reset,
    decode_packet,
    parse_notification,
    CMD_QUERY_BLE_FIRMWARE,
    CMD_QUERY_MCU_FIRMWARE,
    CMD_QUERY_SCHEDULE,
    CMD_QUERY_STATUS,
)

logger = logging.getLogger(__name__)

PAIRING_HINT = (
    "The diffuser only accepts commands from a paired computer. Switch it off, "
    "wait 10 seconds, switch it back on, and retry while the blue light blinks."
)


class DiffuserError(RuntimeError):
    """A diffuser operation failed in a way the caller should report."""


class ResponseTimeout(DiffuserError):
    """The diffuser did not answer the command that was sent."""


@dataclass
class _Waiter:
    """The one response the client is currently waiting for."""

    cmd: int
    future: "asyncio.Future[Optional[Schedule]]"
    schedule_id: Optional[int] = None


def is_airwick_device(
    device: BLEDevice, adv: Optional[AdvertisementData] = None
) -> bool:
    name = device.name or (adv.local_name if adv else "") or ""
    # Anchored, not a substring match -- see is_recognised_device_name.
    if is_recognised_device_name(name):
        return True
    if adv and adv.service_uuids:
        if any(SERVICE_UUID.lower() == s.lower() for s in adv.service_uuids):
            return True
    return False


async def scan_for_diffusers(timeout: float = 6.0) -> List[BLEDevice]:
    """Scan and discover nearby Air Wick diffuser devices."""
    return await AirWickDiffuser.discover(timeout=timeout)


class AirWickDiffuser:
    def __init__(
        self, address_or_device: Optional[Union[str, BLEDevice]] = None
    ) -> None:
        if isinstance(address_or_device, BLEDevice):
            self._device: Optional[BLEDevice] = address_or_device
            self.target_address: Optional[str] = address_or_device.address
        else:
            self._device = None
            self.target_address = address_or_device

        self._client: Optional[BleakClient] = None
        self.status: DiffuserStatus = DiffuserStatus()
        self._waiter: Optional[_Waiter] = None
        self._request_lock: asyncio.Lock = asyncio.Lock()
        self._notification_callbacks: List[Callable[[str, DiffuserStatus], None]] = []

    def add_callback(self, cb: Callable[[str, DiffuserStatus], None]) -> None:
        """Register a callback for notification events."""
        self._notification_callbacks.append(cb)

    @classmethod
    async def discover(cls, timeout: float = 6.0) -> List[BLEDevice]:
        """Scan for nearby Air Wick diffusers."""
        discovered = await BleakScanner.discover(timeout=timeout, return_adv=True)
        results: List[BLEDevice] = []
        for _addr, (device, adv) in discovered.items():
            if is_airwick_device(device, adv):
                results.append(device)
        return results

    async def connect(self, timeout: float = 15.0, auto_sync_time: bool = True) -> None:
        """Connect to the diffuser and subscribe to notifications."""
        if self._client and self._client.is_connected:
            return

        if self._device is None:
            logger.info("Scanning for Air Wick diffuser...")
            if self.target_address:
                target_str = self.target_address
                self._device = await BleakScanner.find_device_by_address(
                    target_str, timeout=timeout
                )
                if not self._device:

                    def match_addr(d: BLEDevice, _adv: AdvertisementData) -> bool:
                        return d.address.lower() == target_str.lower()

                    self._device = await BleakScanner.find_device_by_filter(
                        match_addr, timeout=timeout
                    )
            else:

                def match_airwick(d: BLEDevice, adv: AdvertisementData) -> bool:
                    return is_airwick_device(d, adv)

                self._device = await BleakScanner.find_device_by_filter(
                    match_airwick, timeout=timeout
                )

            if not self._device:
                raise DiffuserError(
                    "Air Wick diffuser not detected. Please ensure it is turned on, within range, and advertising."
                )

            self.target_address = self._device.address
            logger.info(
                f"Found Air Wick diffuser: {self._device.name or 'Unknown'} ({self.target_address})"
            )

        logger.info(f"Connecting to {self.target_address}...")
        self._client = BleakClient(self._device, timeout=timeout)
        try:
            await self._client.connect()
            logger.info("Connected successfully.")

            try:
                await self._client.start_notify(NOTIFY_CHAR_UUID, self._on_notify)
            except Exception as e:
                # Without notifications nothing can be read back, and on an
                # unpaired computer this is the first call the diffuser refuses.
                raise DiffuserError(
                    f"Could not subscribe to diffuser notifications ({e}). "
                    + PAIRING_HINT
                ) from e

            if auto_sync_time:
                try:
                    await self.send_command(encode_set_time())
                except Exception as e:
                    logger.warning(f"Could not sync the diffuser clock: {e}")
        except BaseException:
            # Never leave a half-open connection behind on a failed connect.
            await self.disconnect()
            raise

    async def disconnect(self) -> None:
        """Disconnect from the diffuser. Safe to call in any state."""
        client = self._client
        self._client = None
        self._device = None
        if client is None:
            return
        try:
            if client.is_connected:
                try:
                    await client.stop_notify(NOTIFY_CHAR_UUID)
                except Exception:
                    pass
        finally:
            try:
                await client.disconnect()
                logger.info("Disconnected from diffuser.")
            except Exception as e:
                logger.debug(f"Disconnect notice: {e}")

    @property
    def is_connected(self) -> bool:
        return bool(self._client and self._client.is_connected)

    def _on_notify(
        self, _characteristic: Optional[BleakGATTCharacteristic], data: bytearray
    ) -> None:
        raw = bytes(data)
        decoded = decode_packet(raw)
        event_name, updated_status, maybe_sched = parse_notification(raw, self.status)
        if decoded is None or not event_name:
            return
        self.status = updated_status

        # Only the answer to the command that was sent completes a request. The
        # diffuser also volunteers spray and battery reports at any moment, and
        # those must not be mistaken for it.
        waiter = self._waiter
        if (
            waiter is not None
            and decoded[0] == waiter.cmd
            and not waiter.future.done()
            and (
                waiter.schedule_id is None
                or maybe_sched is None
                or maybe_sched.id == waiter.schedule_id
            )
        ):
            waiter.future.set_result(maybe_sched)

        for cb in self._notification_callbacks:
            try:
                cb(event_name, self.status)
            except Exception as e:
                logger.error(f"Error in notification callback: {e}")

    async def send_command(self, packet: bytes) -> None:
        """Write a raw command packet to the WRITE characteristic."""
        if self._client is None or not self.is_connected:
            raise DiffuserError("Client is not connected")
        await self._client.write_gatt_char(WRITE_CHAR_UUID, packet, response=True)

    async def _request(
        self,
        packet: bytes,
        response_cmd: int,
        timeout: float,
        schedule_id: Optional[int] = None,
    ) -> Optional[Schedule]:
        """Send a query and wait for *its* response. Raise on timeout."""
        async with self._request_lock:
            future: "asyncio.Future[Optional[Schedule]]" = (
                asyncio.get_running_loop().create_future()
            )
            self._waiter = _Waiter(response_cmd, future, schedule_id)
            try:
                await self.send_command(packet)
                try:
                    return await asyncio.wait_for(future, timeout=timeout)
                except asyncio.TimeoutError:
                    raise ResponseTimeout(
                        f"The diffuser did not answer command 0x{response_cmd:02X} "
                        f"within {timeout:g}s."
                    ) from None
            finally:
                self._waiter = None

    async def turn_on(self) -> None:
        """Turn diffuser ON."""
        await self.send_command(encode_set_power(True))
        self.status.power = True

    async def turn_off(self) -> None:
        """Turn diffuser OFF."""
        await self.send_command(encode_set_power(False))
        self.status.power = False

    async def set_intensity(self, intensity: int) -> None:
        """Set intensity level (0: Paused, 1: Low, 2: Med, 3: High, 4: Max)."""
        await self.send_command(encode_set_intensity(intensity))
        self.status.intensity = intensity

    async def boost(self) -> None:
        """Trigger an instant fragrance burst."""
        await self.send_command(encode_boost())

    async def sync_time(self, dt: Optional[datetime] = None) -> None:
        """Synchronize internal RTC clock."""
        await self.send_command(encode_set_time(dt))

    async def reset(self) -> None:
        """Factory reset device."""
        await self.send_command(encode_reset())

    async def set_room_and_fragrance(self, room_id: int, fragrance_id: int) -> None:
        """Set on-device Room ID (1-8) and Fragrance ID (1-33)."""
        await self.send_command(encode_set_room_and_fragrance(room_id, fragrance_id))
        self.status.room_id = room_id
        self.status.fragrance_id = fragrance_id

    async def get_schedule(
        self, schedule_id: int, timeout: float = 2.0
    ) -> Optional[Schedule]:
        """Fetch on-device hardware schedule slot by ID (1-10).

        Raises ResponseTimeout if the diffuser does not answer for this slot.
        """
        return await self._request(
            encode_query_schedule(schedule_id),
            CMD_QUERY_SCHEDULE,
            timeout,
            schedule_id=schedule_id,
        )

    async def get_all_schedules(self, timeout: float = 2.0) -> List[Schedule]:
        """Fetch every occupied hardware slot, enabled or not.

        Occupancy is decided by Schedule.is_set (the window), so a slot that was
        switched off still shows up instead of looking free.
        """
        schedules: List[Schedule] = []
        for i in range(1, 11):
            sched = await self.get_schedule(i, timeout=timeout)
            if sched is not None and sched.is_set:
                schedules.append(sched)
            await asyncio.sleep(0.1)
        return schedules

    async def set_schedule(self, schedule: Schedule) -> None:
        """Write hardware schedule to device memory."""
        await self.send_command(encode_set_schedule(schedule))

    async def delete_schedule(self, schedule_id: int) -> None:
        """Delete hardware schedule by ID."""
        await self.send_command(encode_delete_schedule(schedule_id))

    async def clear_schedules(self) -> None:
        """Clear all on-device hardware schedules."""
        await self.send_command(encode_clear_schedule())

    async def get_status(self, timeout: float = 3.0) -> DiffuserStatus:
        """Fetch status from diffuser. Raises ResponseTimeout if it is silent."""
        await self._request(encode_query_status(), CMD_QUERY_STATUS, timeout)
        return self.status

    async def get_firmware_versions(
        self, timeout: float = 3.0
    ) -> Tuple[Optional[str], Optional[str]]:
        """Fetch MCU and BLE firmware versions (None for one that is not reported)."""
        for packet, cmd in (
            (encode_query_mcu_firmware(), CMD_QUERY_MCU_FIRMWARE),
            (encode_query_ble_firmware(), CMD_QUERY_BLE_FIRMWARE),
        ):
            try:
                await self._request(packet, cmd, timeout)
            except ResponseTimeout as e:
                logger.debug(f"Firmware query unanswered: {e}")
            await asyncio.sleep(0.2)
        return (self.status.mcu_firmware, self.status.ble_firmware)

    async def __aenter__(self) -> "AirWickDiffuser":
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        await self.disconnect()
