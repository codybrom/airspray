import Foundation
import CoreBluetooth

public final class DiffuserManager: NSObject, ObservableObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    public static let shared = DiffuserManager()

    @Published public var isConnected: Bool = false {
        didSet { onStateChange?() }
    }
    @Published public var isScanning: Bool = false {
        didSet { onStateChange?() }
    }
    @Published public var isPoweredOn: Bool = true {
        didSet {
            recordActiveUsage()
            onStateChange?()
        }
    }
    @Published public var isSpraying: Bool = false {
        didSet {
            if isSpraying {
                self.lastSprayTime = Date()
            }
            onStateChange?()
        }
    }
    @Published public var currentIntensity: Int = 2 {
        didSet {
            recordActiveUsage()
            onStateChange?()
        }
    }
    @Published public var batteryLevel: Int = 3 {
        didSet { onStateChange?() }
    }
    @Published public var mcuFirmware: String? = nil {
        didSet { onStateChange?() }
    }
    @Published public var bleFirmware: String? = nil {
        didSet { onStateChange?() }
    }
    /// Slot number -> schedule, as last read from the diffuser.
    @Published public var schedules: [Int: DeviceSchedule] = [:] {
        didSet { onStateChange?() }
    }
    @Published public var isLoadingSchedules: Bool = false {
        didSet { onStateChange?() }
    }
    @Published public var scheduleLoadFailed: Bool = false {
        didSet { onStateChange?() }
    }
    @Published public var scheduleInfo: Int = 0 {
        didSet { onStateChange?() }
    }
    /// The room is device metadata we cannot read back: the diffuser has no BLE
    /// query for it, so the only way it survives a relaunch is our own config.
    @Published public var roomId: Int = 1 {
        didSet {
            if (1...8).contains(roomId) {
                var config = AppConfig.load()
                if config.lastRoomId != roomId {
                    config.lastRoomId = roomId
                    config.save()
                }
            }
            onStateChange?()
        }
    }
    /// Like the room, the fragrance is metadata the diffuser will not report back,
    /// so config is the only place it survives a relaunch.
    @Published public var fragranceId: Int = 1 {
        didSet {
            if (1...33).contains(fragranceId) {
                var config = AppConfig.load()
                if config.lastFragranceId != fragranceId {
                    config.lastFragranceId = fragranceId
                    config.save()
                }
            }
            onStateChange?()
        }
    }
    @Published public var peripheralName: String = "Diffuser"
    @Published public var discoveredDiffusers: [CBPeripheral] = [] {
        didSet { onStateChange?() }
    }
    @Published public var lastSprayTime: Date = Date()

    public var onStateChange: (() -> Void)?

    private var centralManager: CBCentralManager!
    public var targetPeripheral: CBPeripheral?
    private var commandCharacteristic: CBCharacteristic?
    private var scanTimer: Timer?
    private var pendingPowerOn: Bool?
    private var pendingScheduleSlots: Set<Int> = []
    private var scheduleTimeoutTimer: Timer?
    private var usageTimer: Timer?

    private static let serviceCBUUID = CBUUID(string: DiffuserProtocol.serviceUUID)
    private static let commandCBUUID = CBUUID(string: DiffuserProtocol.commandCharUUID)
    private static let notifyCBUUID = CBUUID(string: DiffuserProtocol.notifyCharUUID)

    public override init() {
        super.init()
        let config = AppConfig.load()
        if (1...8).contains(config.lastRoomId) {
            self.roomId = config.lastRoomId
        }
        if (1...33).contains(config.lastFragranceId) {
            self.fragranceId = config.lastFragranceId
        }
        self.centralManager = CBCentralManager(delegate: self, queue: .main)
        startUsageTrackingTimer()
    }

    private func startUsageTrackingTimer() {
        usageTimer?.invalidate()
        usageTimer = Timer.scheduledTimer(withTimeInterval: 15.0, repeats: true) { [weak self] _ in
            self?.recordActiveUsage()
        }
    }

    public func recordActiveUsage() {
        guard isConnected && isPoweredOn else {
            var config = AppConfig.load()
            config.lastActiveTimestamp = nil
            config.save()
            return
        }

        var config = AppConfig.load()
        let now = Date().timeIntervalSince1970
        if let last = config.lastActiveTimestamp, last > 0 {
            let elapsed = now - last
            // Zero at intensity 0: the diffuser is powered but paused, so the
            // bottle is not draining and the level must not tick down.
            let ratePerSec = RefillDepletionModel.consumptionPercentPerSecond(for: currentIntensity)
            let consumed = ratePerSec * elapsed
            config.refillPercentage = max(0.0, config.refillPercentage - consumed)
        }
        config.lastActiveTimestamp = now
        config.save()
    }

    // MARK: - Spray Timing & Countdown Calculation

    /// Estimated gap between sprays. These figures are our own approximations --
    /// the vendor app exposes no spray cadence. Level 0 pauses spraying, so it has
    /// no interval at all.
    public static func sprayInterval(forIntensity level: Int) -> TimeInterval? {
        switch level {
        case 1: return 24.0 * 60.0 // 24m (Low)
        case 2: return 18.0 * 60.0 // 18m (Medium)
        case 3: return 12.0 * 60.0 // 12m (High)
        case 4: return 6.0 * 60.0  // 6m (Max)
        default: return nil
        }
    }

    public var sprayIntervalSeconds: TimeInterval? {
        return Self.sprayInterval(forIntensity: currentIntensity)
    }

    public var secondsUntilNextSpray: Int {
        guard isPoweredOn else { return -1 }
        if isSpraying { return 0 }
        guard let interval = sprayIntervalSeconds else { return -1 }
        let elapsed = Date().timeIntervalSince(lastSprayTime)
        let remainder = interval - elapsed.truncatingRemainder(dividingBy: interval)
        return max(0, Int(remainder))
    }

    public var nextSprayCountdownString: String {
        if !isPoweredOn {
            return "Next Spray: Paused"
        }
        if isSpraying {
            return "Next Spray: Spraying Now..."
        }
        if currentIntensity == 0 { return "Next Spray: Paused" }
        let secs = secondsUntilNextSpray
        if secs < 0 { return "Next Spray: Unknown" }
        let mins = secs / 60
        let remSecs = secs % 60
        if mins > 0 {
            return "Next Spray in: \(mins)m \(remSecs)s"
        } else {
            return "Next Spray in: \(remSecs)s"
        }
    }

    // MARK: - Targeted Connection & Discovery

    public func connectKnownOrScan() {
        guard centralManager.state == .poweredOn else { return }

        let config = AppConfig.load()
        if let lastAddr = config.lastAddress, let uuid = UUID(uuidString: lastAddr) {
            let known = centralManager.retrievePeripherals(withIdentifiers: [uuid])
            if let p = known.first {
                self.targetPeripheral = p
                self.peripheralName = p.name ?? "Diffuser"
                self.centralManager.connect(p, options: nil)
            }
        }

        startTimedScan(duration: 20.0)
    }

    public func startTimedScan(duration: TimeInterval = 20.0) {
        guard centralManager.state == .poweredOn else { return }
        stopScanning()

        self.isScanning = true
        centralManager.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])

        scanTimer?.invalidate()
        scanTimer = Timer.scheduledTimer(withTimeInterval: duration, repeats: false) { [weak self] _ in
            self?.stopScanning()
        }
    }

    public func stopScanning() {
        scanTimer?.invalidate()
        scanTimer = nil
        if isScanning {
            centralManager.stopScan()
            self.isScanning = false
        }
    }

    public func scanForNewDiffuser() {
        guard centralManager.state == .poweredOn else { return }
        if let current = targetPeripheral {
            centralManager.cancelPeripheralConnection(current)
            self.targetPeripheral = nil
        }
        self.isConnected = false
        self.discoveredDiffusers.removeAll()
        startTimedScan(duration: 20.0)
    }

    public func selectDiffuser(peripheral: CBPeripheral) {
        if let current = targetPeripheral {
            centralManager.cancelPeripheralConnection(current)
        }
        self.targetPeripheral = peripheral
        self.peripheralName = peripheral.name ?? "Diffuser"
        stopScanning()
        self.centralManager.connect(peripheral, options: nil)
    }

    public func setPower(on: Bool) {
        guard sendCommand(DiffuserProtocol.encodeSetPower(on: on)) else {
            // Waking from sleep, the screen unlocks before Bluetooth reconnects, so
            // this command would be silently discarded and the diffuser would stay
            // off until the user noticed. Hold the intent and apply it on connect.
            // Deliberately not updating isPoweredOn: the diffuser never heard this.
            pendingPowerOn = on
            return
        }
        pendingPowerOn = nil
        self.isPoweredOn = on
        if on {
            self.lastSprayTime = Date()
        }
    }

    /// Replay a power command that could not be delivered while disconnected.
    private func applyPendingPower() {
        guard let desired = pendingPowerOn else { return }
        pendingPowerOn = nil
        setPower(on: desired)
    }

    public func setIntensity(level: Int) {
        self.currentIntensity = level
        let data = DiffuserProtocol.encodeSetIntensity(level: level)
        sendCommand(data)
    }

    public func sendBoost() {
        self.isSpraying = true
        self.lastSprayTime = Date()
        var config = AppConfig.load()
        config.refillPercentage = max(0.0, config.refillPercentage - RefillDepletionModel.boostConsumptionPercent)
        config.totalPuffsCount += 1
        config.save()
        let data = DiffuserProtocol.encodeBoost()
        sendCommand(data)
    }

    public func setRoom(id: Int) {
        self.roomId = id
        let data = DiffuserProtocol.encodeSetRoomAndFragrance(roomId: id, fragranceId: self.fragranceId)
        sendCommand(data)
    }

    public func setFragrance(id: Int) {
        self.fragranceId = id
        let data = DiffuserProtocol.encodeSetRoomAndFragrance(roomId: self.roomId, fragranceId: id)
        sendCommand(data)
    }

    public func queryAll() {
        sendCommand(DiffuserProtocol.encodeSetTime())
        sendCommand(DiffuserProtocol.encodeQueryStatus())
        sendCommand(DiffuserProtocol.encodeQueryMcuFirmware())
        sendCommand(DiffuserProtocol.encodeQueryBleFirmware())
    }

    public static let scheduleSlotCount = 10
    /// Gap between slot queries; 150ms reads all ten reliably, 0ms loses slot 1.
    private static let scheduleQueryInterval: TimeInterval = 0.15

    /// Read every slot from the diffuser. Ten round-trips, so the UI shows a
    /// loading state until the last one lands or the deadline passes.
    public func refreshSchedules() {
        guard isConnected, commandCharacteristic != nil else {
            self.schedules = [:]
            self.isLoadingSchedules = false
            self.scheduleLoadFailed = true
            return
        }

        scheduleTimeoutTimer?.invalidate()
        self.schedules = [:]
        self.pendingScheduleSlots = Set(1...Self.scheduleSlotCount)
        self.scheduleLoadFailed = false
        self.isLoadingSchedules = true

        // Ten writes back to back overruns the diffuser: it consistently drops the
        // first one, so slot 1 never answers and the load never completes. Four in a
        // row is fine, so this is a depth limit rather than a general pacing need.
        for (index, slot) in (1...Self.scheduleSlotCount).enumerated() {
            DispatchQueue.main.asyncAfter(deadline: .now() + Double(index) * Self.scheduleQueryInterval) { [weak self] in
                guard let self, self.isLoadingSchedules else { return }
                self.sendCommand(DiffuserProtocol.encodeQuerySchedule(slot: slot))
            }
        }

        scheduleTimeoutTimer = Timer.scheduledTimer(withTimeInterval: 6.0, repeats: false) { [weak self] _ in
            guard let self, self.isLoadingSchedules else { return }
            // Partial data would misreport which slots are free, so treat it as failure.
            self.isLoadingSchedules = false
            self.scheduleLoadFailed = !self.pendingScheduleSlots.isEmpty
        }
    }

    public func saveSchedule(_ schedule: DeviceSchedule) {
        // Cache what the diffuser was actually sent, not what was asked for.
        let stored = schedule.normalizedForDevice()
        sendCommand(DiffuserProtocol.encodeSetSchedule(stored))
        self.schedules[stored.slot] = stored
    }

    public func deleteSchedule(slot: Int) {
        sendCommand(DiffuserProtocol.encodeDeleteSchedule(slot: slot))
        self.schedules.removeValue(forKey: slot)
    }

    /// Lowest slot with nothing configured, or nil when all ten are in use.
    public var firstFreeScheduleSlot: Int? {
        (1...Self.scheduleSlotCount).first { schedules[$0]?.isConfigured != true }
    }

    public func clearAllSchedules() {
        let data = DiffuserProtocol.encodeClearAllSchedules()
        sendCommand(data)
    }

    /// Returns false when the link is down, so callers can tell a dropped command
    /// from a delivered one instead of assuming it landed.
    @discardableResult
    private func sendCommand(_ data: Data) -> Bool {
        guard let p = targetPeripheral, let char = commandCharacteristic else { return false }
        p.writeValue(data, for: char, type: .withoutResponse)
        return true
    }

    // MARK: - CBCentralManagerDelegate

    public func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state == .poweredOn {
            connectKnownOrScan()
        }
    }

    public func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String : Any], rssi RSSI: NSNumber) {
        let rawName = peripheral.name ?? (advertisementData[CBAdvertisementDataLocalNameKey] as? String) ?? ""
        let serviceUUIDs = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? []
        let config = AppConfig.load()

        let isMatch = DiffuserProtocol.isRecognisedDeviceName(rawName) ||
                      serviceUUIDs.contains(Self.serviceCBUUID) ||
                      (config.lastAddress != nil && peripheral.identifier.uuidString == config.lastAddress)

        if isMatch {
            if !discoveredDiffusers.contains(where: { $0.identifier == peripheral.identifier }) {
                discoveredDiffusers.append(peripheral)
            }
            if targetPeripheral == nil || targetPeripheral?.state != .connected {
                self.targetPeripheral = peripheral
                self.peripheralName = rawName.isEmpty ? "Diffuser" : rawName
                stopScanning()
                self.centralManager.connect(peripheral, options: nil)
            }
        }
    }

    public func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        self.isConnected = true
        self.lastSprayTime = Date()
        stopScanning()

        var config = AppConfig.load()
        config.lastAddress = peripheral.identifier.uuidString
        config.lastActiveTimestamp = Date().timeIntervalSince1970
        config.save()

        peripheral.delegate = self
        peripheral.discoverServices([Self.serviceCBUUID])
    }

    public func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        self.isConnected = false
        self.commandCharacteristic = nil
        var config = AppConfig.load()
        config.lastActiveTimestamp = nil
        config.save()

        DispatchQueue.main.asyncAfter(deadline: .now() + 3.0) { [weak self] in
            if self?.isConnected == false && self?.isScanning == false {
                self?.connectKnownOrScan()
            }
        }
    }

    // MARK: - CBPeripheralDelegate

    public func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard let services = peripheral.services else { return }
        for service in services where service.uuid == Self.serviceCBUUID {
            peripheral.discoverCharacteristics([Self.commandCBUUID, Self.notifyCBUUID], for: service)
        }
    }

    public func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard let characteristics = service.characteristics else { return }
        for char in characteristics {
            if char.uuid == Self.commandCBUUID {
                self.commandCharacteristic = char
                applyPendingPower()
                queryAll()
            } else if char.uuid == Self.notifyCBUUID {
                peripheral.setNotifyValue(true, for: char)
            }
        }
    }

    public func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard let data = characteristic.value,
              let packet = DiffuserProtocol.decodePacket(data) else { return }
        let cmd = packet.cmd
        let payload = packet.payload

        DispatchQueue.main.async {
            switch cmd {
            case DiffuserProtocol.cmdQueryStatus:
                if payload.count >= 4 {
                    self.isPoweredOn = payload[0] == 1
                    self.isSpraying = payload[1] == 1
                    if (0...4).contains(Int(payload[2])) {
                        self.currentIntensity = Int(payload[2])
                    }
                    if (0...4).contains(Int(payload[3])) {
                        self.batteryLevel = Int(payload[3])
                    }
                }
                if payload.count >= 6, (0...26).contains(Int(payload[5])) {
                    self.scheduleInfo = Int(payload[5])
                }
            case DiffuserProtocol.cmdQuerySchedule, DiffuserProtocol.cmdSetSchedule:
                if let schedule = DiffuserProtocol.decodeSchedule(payload: payload) {
                    self.schedules[schedule.slot] = schedule
                    self.pendingScheduleSlots.remove(schedule.slot)
                    if self.pendingScheduleSlots.isEmpty && self.isLoadingSchedules {
                        self.scheduleTimeoutTimer?.invalidate()
                        self.isLoadingSchedules = false
                    }
                }
            case DiffuserProtocol.cmdSprayReport:
                if payload.count >= 1 {
                    self.isSpraying = payload[0] == 1
                    if self.isSpraying {
                        var config = AppConfig.load()
                        config.totalPuffsCount += 1
                        config.save()
                    }
                }
            case DiffuserProtocol.cmdBatteryReport:
                if payload.count >= 1,
                   DiffuserProtocol.shouldAcceptBatteryLevel(Int(payload[0]), previous: self.batteryLevel) {
                    self.batteryLevel = Int(payload[0])
                }
            case DiffuserProtocol.cmdSetPower:
                if payload.count >= 1 {
                    self.isPoweredOn = payload[0] == 1
                }
            case DiffuserProtocol.cmdSetIntensity:
                if payload.count >= 1, (0...4).contains(Int(payload[0])) {
                    self.currentIntensity = Int(payload[0])
                }
            case DiffuserProtocol.cmdSetRoomFragrance:
                if payload.count >= 2 {
                    self.roomId = Int(payload[0])
                    self.fragranceId = Int(payload[1])
                }
            case DiffuserProtocol.cmdQueryMcuFirmware:
                if payload.count >= 4 {
                    self.mcuFirmware = "\(payload[0]).\(String(format: "%02d", payload[1])),\(payload[2]).\(String(format: "%02d", payload[3]))"
                }
            case DiffuserProtocol.cmdQueryBleFirmware:
                if payload.count >= 4 {
                    self.bleFirmware = "\(payload[0]).\(String(format: "%02d", payload[1])),\(payload[2]).\(String(format: "%02d", payload[3]))"
                }
            default:
                break
            }
        }
    }
}
