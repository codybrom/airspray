"""Tests for AirWickDiffuser helper functions."""

from unittest.mock import MagicMock
from bleak.backends.device import BLEDevice
from bleak.backends.scanner import AdvertisementData
from airspray.client import is_airwick_device
from airspray.protocol import SERVICE_UUID


def test_is_airwick_device_by_name() -> None:
    dev = MagicMock(spec=BLEDevice)
    dev.name = "Air Wick Mist"
    assert is_airwick_device(dev, None) is True

    dev2 = MagicMock(spec=BLEDevice)
    dev2.name = "AIRWICK REESES"
    assert is_airwick_device(dev2, None) is True

    dev3 = MagicMock(spec=BLEDevice)
    dev3.name = "Unknown Bluetooth Speaker"
    assert is_airwick_device(dev3, None) is False


def test_is_airwick_device_by_service_uuid() -> None:
    dev = MagicMock(spec=BLEDevice)
    dev.name = ""

    adv = AdvertisementData(
        local_name=None,
        manufacturer_data={},
        service_data={},
        service_uuids=[SERVICE_UUID],
        tx_power=None,
        rssi=-70,
        platform_data=(),
    )
    assert is_airwick_device(dev, adv) is True
