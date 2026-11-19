import Foundation
import CoreBluetooth

public final class DiffuserManager: NSObject, ObservableObject, CBCentralManagerDelegate, CBPeripheralDelegate {
    public static let shared = DiffuserManager()

    // MARK: - Published state
    //
    // Observers only run when a value actually changes: the diffuser repeats its
    // status often, and each notification would otherwise rebuild the menu and
    // touch the config file for nothing.

    /// The BLE link to the chosen diffuser is up. Commands are not accepted
    /// until `isReady`.
    @Published public private(set) var isConnected: Bool = false {
        didSet { if oldValue != isConnected { syncUsage(); notifyChange() } }
    }
    /// Notifications are enabled and the first queries have been sent, so
    /// commands can be delivered and their answers heard.
    @Published public private(set) var isReady: Bool = false {
        didSet { if oldValue != isReady { notifyChange() } }
    }
    /// A status packet has arrived on this connection. Until then power,
    /// intensity and battery are placeholders and must not be shown as fact.
    @Published public private(set) var hasDeviceState: Bool = false {
        didSet { if oldValue != hasDeviceState { syncUsage(); notifyChange() } }
    }
    /// The diffuser refuses the link until it is paired again. See
    /// `pairingHelpLines`.
    @Published public private(set) var needsPairing: Bool = false {
        didSet { if oldValue != needsPairing { notifyChange() } }
    }
    @Published public private(set) var isScanning: Bool = false {
        didSet { if oldValue != isScanning { notifyChange() } }
    }
    @Published public private(set) var isPoweredOn: Bool = true {
        didSet { if oldValue != isPoweredOn { syncUsage(); notifyChange() } }
    }
    /// A power command waiting for the link. Nil when nothing is queued.
    @Published public private(set) var pendingPowerOn: Bool? = nil {
        didSet { if oldValue != pendingPowerOn { notifyChange() } }
    }
    @Published public private(set) var isSpraying: Bool = false {
        didSet {
            guard oldValue != isSpraying else { return }
            if isSpraying { lastSprayTime = Date() }
            notifyChange()
        }
    }
    @Published public private(set) var currentIntensity: Int = 2 {
        didSet { if oldValue != currentIntensity { syncUsage(); notifyChange() } }
    }
    @Published public private(set) var batteryLevel: Int = 3 {
        didSet { if oldValue != batteryLevel { notifyChange() } }
    }
    @Published public private(set) var mcuFirmware: String? = nil {
        didSet { if oldValue != mcuFirmware { notifyChange() } }
    }
    @Published public private(set) var bleFirmware: String? = nil {
        didSet { if oldValue != bleFirmware { notifyChange() } }
    }
    /// Slot number -> schedule, as last read from the diffuser.
    @Published public private(set) var schedules: [Int: DeviceSchedule] = [:] {
        didSet { if oldValue != schedules { notifyChange() } }
    }
    @Published public private(set) var isLoadingSchedules: Bool = false {
        didSet { if oldValue != isLoadingSchedules { notifyChange() } }
    }
    @Published public private(set) var scheduleLoadFailed: Bool = false {
        didSet { if oldValue != scheduleLoadFailed { notifyChange() } }
    }
    @Published public private(set) var scheduleInfo: Int = 0 {
        didSet { if oldValue != scheduleInfo { notifyChange() } }
    }
    /// The room is device metadata we cannot read back: the diffuser has no BLE
    /// query for it, so the only way it survives a relaunch is our own config.
    @Published public private(set) var roomId: Int = 1 {
        didSet { if oldValue != roomId { notifyChange() } }
    }
    /// Like the room, the fragrance is metadata the diffuser will not report back,
    /// so config is the only place it survives a relaunch.
    @Published public private(set) var fragranceId: Int = 1 {
        didSet { if oldValue != fragranceId { notifyChange() } }
    }
    @Published public private(set) var peripheralName: String = "Diffuser" {
        didSet { if oldValue != peripheralName { notifyChange() } }
    }
    @Published public private(set) var discoveredDiffusers: [CBPeripheral] = [] {
        didSet { notifyChange() }
    }
    @Published public private(set) var lastSprayTime: Date = Date()

    public var onStateChange: (() -> Void)?

    /// What to tell the user when `needsPairing` is set.
    public static let pairingHelpLines = [
        "Turn the diffuser off and on until its light blinks blue (pairing mode).",
        "If it still will not connect, forget it in System Settings > Bluetooth.",
    ]

    private var centralManager: CBCentralManager!
    public private(set) var targetPeripheral: CBPeripheral?
    private var commandCharacteristic: CBCharacteristic?

    // Scanning
    private enum ScanPurpose { case reconnect, discovery }
    private var scanPurpose: ScanPurpose = .reconnect
    private var scanTimer: Timer?
    /// The paired diffuser's identifier, cached so matching an advertisement
    /// does not read the config file once per packet from every nearby device.
    private var knownAddress: String?

    // Reconnecting
    private var policy = ReconnectPolicy()
    private var reconnectWork: DispatchWorkItem?
    /// Whether this connection has delivered a decoded packet.
    private var receivedPacket = false
    /// A GATT operation on this connection failed for want of encryption.
    private var sawPairingError = false
    /// Peripherals whose disconnect was requested by the app, not the radio.
    private var requestedDisconnects = Set<UUID>()
    private var packetCount = 0

    // First contact
    private var statusRetryTimer: Timer?
    private var statusRetriesLeft = 0
    /// A queued power command that was replayed on connect, kept until a status
    /// packet shows whether the diffuser heard it.
    private var powerAwaitingConfirmation: Bool?
    private static let statusRetryInterval: TimeInterval = 2.5
    private static let statusRetryLimit = 4

    // Schedules
    private var scheduleLoad: ScheduleLoadTracker?
    private var scheduleLoadGeneration = 0
    private var scheduleLoadStartPacketCount = 0
    private var scheduleTimeoutTimer: Timer?

    // Usage accounting
    private var usageTimer: Timer?
    private var usageActive = false
    private var usageIntensity = 0
    private var usageSince: TimeInterval = 0
    /// Spraying as last stated by a spray report, separate from `isSpraying`,
    /// which a boost sets optimistically.
    private var lastReportedSpraying = false

    private static let serviceCBUUID = CBUUID(string: DiffuserProtocol.serviceUUID)
    private static let commandCBUUID = CBUUID(string: DiffuserProtocol.commandCharUUID)
    private static let notifyCBUUID = CBUUID(string: DiffuserProtocol.notifyCharUUID)

    public override init() {
        super.init()
        let config = AppConfig.load()
        if DiffuserProtocol.isValidRoomId(config.lastRoomId) {
            self.roomId = config.lastRoomId
        }
        if DiffuserProtocol.isValidFragranceId(config.lastFragranceId) {
            self.fragranceId = config.lastFragranceId
        }
        self.knownAddress = config.lastAddress
        self.centralManager = CBCentralManager(delegate: self, queue: .main)
        startUsageTrackingTimer()
    }

    private func notifyChange() {
        onStateChange?()
    }

    private static func commonModeTimer(interval: TimeInterval, repeats: Bool,
                                        _ block: @escaping () -> Void) -> Timer {
        // .common so the timer still fires while a menu is being tracked.
        let timer = Timer(timeInterval: interval, repeats: repeats) { _ in block() }
        RunLoop.main.add(timer, forMode: .common)
        return timer
    }

    // MARK: - Refill usage accounting

    private func startUsageTrackingTimer() {
        usageTimer?.invalidate()
        usageTimer = Self.commonModeTimer(interval: 15.0, repeats: true) { [weak self] in
            self?.syncUsage()
        }
    }

    /// Whether the bottle is draining right now, as far as we can tell.
    private var isConsuming: Bool {
        isConnected && hasDeviceState && isPoweredOn
            && RefillDepletionModel.consumptionPercentPerSecond(for: currentIntensity) > 0
    }

    /// Bill the time since the last sync to the refill level.
    ///
    /// Usage is tracked as segments. A segment is billed at the intensity it ran
    /// at -- this is called after a change, so the state that just ended is the
    /// one remembered in `usageIntensity`, not the current one. While nothing is
    /// draining (disconnected, paused, unknown state) this does no file I/O.
    public func syncUsage() {
        let now = Date().timeIntervalSince1970
        let consuming = isConsuming

        if usageActive {
            let rate = RefillDepletionModel.consumptionPercentPerSecond(for: usageIntensity)
            let memorySince = usageSince
            AppConfig.update { config in
                // The CLI advances this timestamp when it bills usage itself.
                // Starting from the later of the two avoids billing twice.
                let since = max(config.lastActiveTimestamp ?? 0, memorySince)
                let elapsed = max(0, now - since)
                config.refillPercentage = max(0.0, config.refillPercentage - rate * elapsed)
                config.lastActiveTimestamp = consuming ? now : nil
            }
        } else if consuming {
            AppConfig.update { $0.lastActiveTimestamp = now }
        }

        usageActive = consuming
        usageIntensity = currentIntensity
        usageSince = now
    }

    /// Close the open usage segment before the app exits.
    public func shutdown() {
        syncUsage()
        if usageActive {
            usageActive = false
            AppConfig.update { $0.lastActiveTimestamp = nil }
        }
    }

    // MARK: - Spray Timing & Countdown Calculation

    /// Estimated gap between sprays. These figures are our own approximations --
    /// the original app exposes no spray cadence. Level 0 pauses spraying, so it has
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
        guard hasDeviceState else { return "Next Spray: Unknown" }
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

    /// Connect to the chosen (or last paired) diffuser, optionally scanning too.
    ///
    /// A connect request to a known peripheral stays pending inside CoreBluetooth
    /// until the diffuser appears, so a scan is only needed to find a diffuser we
    /// have no identifier for, or to list others.
    public func connectKnownOrScan(scan: Bool = true) {
        guard centralManager.state == .poweredOn else { return }
        guard !isConnected else { return }

        if targetPeripheral == nil,
           let address = knownAddress, let uuid = UUID(uuidString: address),
           let known = centralManager.retrievePeripherals(withIdentifiers: [uuid]).first {
            targetPeripheral = known
            peripheralName = known.name ?? "Diffuser"
            noteDiscovered(known)
        }

        if let target = targetPeripheral {
            centralManager.connect(target, options: nil)
        }

        if scan || targetPeripheral == nil {
            startTimedScan(duration: 20.0, purpose: .reconnect)
        }
    }

    private func startTimedScan(duration: TimeInterval, purpose: ScanPurpose) {
        guard centralManager.state == .poweredOn else { return }
        stopScanning()

        scanPurpose = purpose
        isScanning = true
        centralManager.scanForPeripherals(withServices: nil, options: [CBCentralManagerScanOptionAllowDuplicatesKey: false])

        scanTimer = Self.commonModeTimer(interval: duration, repeats: false) { [weak self] in
            self?.scanTimedOut()
        }
    }

    public func stopScanning() {
        scanTimer?.invalidate()
        scanTimer = nil
        if isScanning {
            if centralManager.state == .poweredOn {
                centralManager.stopScan()
            }
            isScanning = false
        }
    }

    /// A scan that ends with no link must leave a retry behind it. Otherwise a
    /// retry that came due mid-scan is the last attempt ever made.
    private func scanTimedOut() {
        stopScanning()
        if !isConnected && reconnectWork == nil {
            scheduleReconnect(after: policy.idleDelay)
        }
    }

    /// Look for diffusers without giving up the current connection, so a second
    /// one can be listed and switched to. With no connection, the first match
    /// (or the paired diffuser) is connected as usual.
    public func scanForNewDiffuser() {
        guard centralManager.state == .poweredOn else { return }
        resetReconnectBackoff()
        discoveredDiffusers = discoveredDiffusers.filter {
            $0.identifier == targetPeripheral?.identifier
        }
        if !isConnected {
            needsPairing = false
            connectKnownOrScan(scan: false)
        }
        startTimedScan(duration: 20.0, purpose: .discovery)
    }

    public func selectDiffuser(peripheral: CBPeripheral) {
        if peripheral.identifier == targetPeripheral?.identifier && isConnected { return }
        if let current = targetPeripheral, current.identifier != peripheral.identifier {
            requestedDisconnects.insert(current.identifier)
            centralManager.cancelPeripheralConnection(current)
            tearDownLink()
        }
        targetPeripheral = peripheral
        peripheralName = peripheral.name ?? "Diffuser"
        needsPairing = false
        resetReconnectBackoff()
        noteDiscovered(peripheral)
        centralManager.connect(peripheral, options: nil)
    }

    /// The Mac woke or was unlocked: whatever backoff was running belongs to the
    /// past, so try again now rather than up to five minutes from now.
    public func reconnectNow() {
        resetReconnectBackoff()
        guard centralManager.state == .poweredOn, !isConnected else { return }
        connectKnownOrScan(scan: true)
    }

    /// The user asked for a connection, or the link is working: retry promptly again.
    private func resetReconnectBackoff() {
        policy.reset()
        reconnectWork?.cancel()
        reconnectWork = nil
    }

    private func scheduleReconnect(after delay: TimeInterval) {
        reconnectWork?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.reconnectDue() }
        reconnectWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + delay, execute: work)
    }

    private func reconnectDue() {
        reconnectWork = nil
        guard !isConnected else { return }
        if isScanning {
            // Don't cut a scan short, but don't let this attempt evaporate either.
            scheduleReconnect(after: ReconnectPolicy.baseDelay)
            return
        }
        connectKnownOrScan(scan: targetPeripheral == nil)
    }

    private func noteDiscovered(_ peripheral: CBPeripheral) {
        if !discoveredDiffusers.contains(where: { $0.identifier == peripheral.identifier }) {
            discoveredDiffusers.append(peripheral)
        }
    }

    /// Forget everything that belonged to the link that just ended.
    private func tearDownLink() {
        statusRetryTimer?.invalidate()
        statusRetryTimer = nil
        commandCharacteristic = nil
        powerAwaitingConfirmation = nil
        lastReportedSpraying = false
        receivedPacket = false
        sawPairingError = false
        isReady = false
        isSpraying = false
        hasDeviceState = false
        isConnected = false
        clearScheduleState()
    }

    /// Record how a connection (or attempt) ended and arrange the next one.
    private func linkEnded(error: Error?, receivedPacket: Bool, sawPairingError: Bool) {
        let pairingFailure = sawPairingError || ReconnectPolicy.isPairingFailure(error)
        if pairingFailure {
            needsPairing = true
        }
        let outcome = ReconnectPolicy.outcome(receivedPacket: receivedPacket, pairingFailure: pairingFailure)
        scheduleReconnect(after: policy.nextDelay(after: outcome))
    }

    // MARK: - Commands
    //
    // Local state only changes when the command was actually written. A command
    // that could not be sent returns false and leaves everything as it was.

    /// The power state the user has asked for, delivered or not.
    public var effectivePowerOn: Bool {
        pendingPowerOn ?? isPoweredOn
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
        isPoweredOn = on
        if on {
            lastSprayTime = Date()
        }
    }

    /// Drop a queued power command that is no longer wanted.
    public func cancelPendingPower() {
        pendingPowerOn = nil
    }

    /// Replay a power command that could not be delivered while disconnected.
    private func applyPendingPower() {
        guard let desired = pendingPowerOn else { return }
        setPower(on: desired)
        if pendingPowerOn == nil {
            powerAwaitingConfirmation = desired
        }
    }

    @discardableResult
    public func setIntensity(level: Int) -> Bool {
        guard sendCommand(DiffuserProtocol.encodeSetIntensity(level: level)) else { return false }
        currentIntensity = max(0, min(4, level))
        return true
    }

    /// The spray itself is counted when the diffuser reports it. This only
    /// accounts for the extra liquid a manual burst uses.
    @discardableResult
    public func sendBoost() -> Bool {
        guard sendCommand(DiffuserProtocol.encodeBoost()) else { return false }
        isSpraying = true
        lastSprayTime = Date()
        AppConfig.update { config in
            config.refillPercentage = max(0.0, config.refillPercentage - RefillDepletionModel.boostConsumptionPercent)
        }
        return true
    }

    @discardableResult
    public func setRoom(id: Int) -> Bool {
        guard DiffuserProtocol.isValidRoomId(id),
              sendCommand(DiffuserProtocol.encodeSetRoomAndFragrance(roomId: id, fragranceId: fragranceId))
        else { return false }
        roomId = id
        persistRoomAndFragrance()
        return true
    }

    @discardableResult
    public func setFragrance(id: Int) -> Bool {
        guard DiffuserProtocol.isValidFragranceId(id),
              sendCommand(DiffuserProtocol.encodeSetRoomAndFragrance(roomId: roomId, fragranceId: id))
        else { return false }
        fragranceId = id
        persistRoomAndFragrance()
        return true
    }

    private func persistRoomAndFragrance() {
        let room = roomId, fragrance = fragranceId
        AppConfig.update { config in
            config.lastRoomId = room
            config.lastFragranceId = fragrance
        }
    }

    public func queryAll() {
        sendCommand(DiffuserProtocol.encodeSetTime())
        sendCommand(DiffuserProtocol.encodeQueryStatus())
        sendCommand(DiffuserProtocol.encodeQueryMcuFirmware())
        sendCommand(DiffuserProtocol.encodeQueryBleFirmware())
    }

    /// Returns false when the link is not ready, so callers can tell a dropped
    /// command from a delivered one instead of assuming it landed.
    @discardableResult
    private func sendCommand(_ data: Data) -> Bool {
        guard isReady, let p = targetPeripheral, p.state == .connected,
              let char = commandCharacteristic else { return false }
        p.writeValue(data, for: char, type: .withoutResponse)
        return true
    }

    // MARK: - First contact

    /// Notifications are on, so answers can be heard: only now is it worth
    /// asking anything. Writing as soon as the command characteristic appeared
    /// sent the queries into a link that could not yet deliver the replies.
    private func linkBecameReady() {
        guard !isReady else { return }
        isReady = true
        applyPendingPower()
        queryAll()

        // Pairing can complete after these first writes, in which case they were
        // dropped. Ask again a few times until a status packet shows up.
        statusRetriesLeft = Self.statusRetryLimit
        statusRetryTimer?.invalidate()
        statusRetryTimer = Self.commonModeTimer(interval: Self.statusRetryInterval, repeats: true) { [weak self] in
            guard let self else { return }
            if self.hasDeviceState || !self.isReady || self.statusRetriesLeft <= 0 {
                self.statusRetryTimer?.invalidate()
                self.statusRetryTimer = nil
                return
            }
            self.statusRetriesLeft -= 1
            self.queryAll()
        }
    }

    // MARK: - Schedules

    public static let scheduleSlotCount = 10
    /// Gap between slot queries. 150ms reads all ten reliably, 0ms loses slot 1.
    private static let scheduleQueryInterval: TimeInterval = 0.15
    private static let scheduleFirstPassDeadline: TimeInterval = 3.0
    private static let scheduleSecondPassGrace: TimeInterval = 2.0

    /// Read every slot from the diffuser. Ten round-trips, so the UI shows a
    /// loading state until every slot has answered or been identified as empty.
    public func refreshSchedules() {
        // Invalidates the staggered queries and deadline of any earlier load, so
        // two loads never interleave their writes.
        scheduleLoadGeneration += 1
        let generation = scheduleLoadGeneration
        scheduleTimeoutTimer?.invalidate()
        scheduleTimeoutTimer = nil

        guard isReady else {
            schedules = [:]
            scheduleLoad = nil
            isLoadingSchedules = false
            scheduleLoadFailed = true
            return
        }

        schedules = [:]
        scheduleLoad = ScheduleLoadTracker(slots: 1...Self.scheduleSlotCount)
        scheduleLoadStartPacketCount = packetCount
        scheduleLoadFailed = false
        isLoadingSchedules = true

        sendScheduleQueries(for: Array(1...Self.scheduleSlotCount), generation: generation)

        scheduleTimeoutTimer = Self.commonModeTimer(interval: Self.scheduleFirstPassDeadline, repeats: false) { [weak self] in
            self?.scheduleFirstPassEnded(generation: generation)
        }
    }

    /// Ten writes back to back overruns the diffuser: it consistently drops the
    /// first one. Four in a row is fine, so this is a depth limit rather than a
    /// general pacing need.
    private func sendScheduleQueries(for slots: [Int], generation: Int) {
        for (index, slot) in slots.enumerated() {
            DispatchQueue.main.asyncAfter(deadline: .now() + Double(index) * Self.scheduleQueryInterval) { [weak self] in
                guard let self, self.scheduleLoadGeneration == generation,
                      self.scheduleLoad?.pending.contains(slot) == true else { return }
                if self.sendCommand(DiffuserProtocol.encodeQuerySchedule(slot: slot)) {
                    self.scheduleLoad?.didQuery(slot: slot)
                }
            }
        }
    }

    /// Some slots have not answered. Ask for just those once more, with a status
    /// query alongside so the second deadline can tell a quiet slot from a dead
    /// link.
    private func scheduleFirstPassEnded(generation: Int) {
        guard scheduleLoadGeneration == generation, isLoadingSchedules,
              let load = scheduleLoad, !load.isComplete else { return }

        let remaining = load.pending.sorted()
        sendCommand(DiffuserProtocol.encodeQueryStatus())
        sendScheduleQueries(for: remaining, generation: generation)

        let deadline = Double(remaining.count) * Self.scheduleQueryInterval + Self.scheduleSecondPassGrace
        scheduleTimeoutTimer = Self.commonModeTimer(interval: deadline, repeats: false) { [weak self] in
            self?.scheduleSecondPassEnded(generation: generation)
        }
    }

    private func scheduleSecondPassEnded(generation: Int) {
        guard scheduleLoadGeneration == generation, isLoadingSchedules else { return }
        scheduleTimeoutTimer = nil

        // Nothing at all heard since the load began means the link is not
        // delivering, and an empty list would misreport every slot as free.
        guard isReady, packetCount != scheduleLoadStartPacketCount else {
            scheduleLoad = nil
            isLoadingSchedules = false
            scheduleLoadFailed = true
            return
        }

        // The link is alive and these slots stayed silent through two queries:
        // they are free slots the firmware does not answer for.
        scheduleLoad?.markSilentSlotsEmpty()
        finishScheduleLoadIfComplete()
    }

    private func finishScheduleLoadIfComplete() {
        guard isLoadingSchedules, scheduleLoad?.isComplete == true else { return }
        scheduleTimeoutTimer?.invalidate()
        scheduleTimeoutTimer = nil
        scheduleLoadGeneration += 1
        isLoadingSchedules = false
        scheduleLoadFailed = false
    }

    /// Schedules live on the diffuser. A cached copy from a link that has gone
    /// is not something the user can act on.
    private func clearScheduleState() {
        scheduleLoadGeneration += 1
        scheduleTimeoutTimer?.invalidate()
        scheduleTimeoutTimer = nil
        scheduleLoad = nil
        schedules = [:]
        isLoadingSchedules = false
        scheduleLoadFailed = false
    }

    @discardableResult
    public func saveSchedule(_ schedule: DeviceSchedule) -> Bool {
        // Cache what the diffuser was actually sent, not what was asked for.
        let stored = schedule.normalizedForDevice()
        guard sendCommand(DiffuserProtocol.encodeSetSchedule(stored)) else { return false }
        schedules[stored.slot] = stored
        return true
    }

    @discardableResult
    public func deleteSchedule(slot: Int) -> Bool {
        guard sendCommand(DiffuserProtocol.encodeDeleteSchedule(slot: slot)) else { return false }
        schedules.removeValue(forKey: slot)
        return true
    }

    /// Lowest slot with nothing configured, or nil when all ten are in use.
    public var firstFreeScheduleSlot: Int? {
        (1...Self.scheduleSlotCount).first { schedules[$0]?.isConfigured != true }
    }

    @discardableResult
    public func clearAllSchedules() -> Bool {
        guard sendCommand(DiffuserProtocol.encodeClearAllSchedules()) else { return false }
        schedules = [:]
        return true
    }

    // MARK: - CBCentralManagerDelegate

    public func centralManagerDidUpdateState(_ central: CBCentralManager) {
        if central.state == .poweredOn {
            connectKnownOrScan()
        } else {
            // Bluetooth went away: peripheral objects are no longer usable and no
            // disconnect callback is promised.
            reconnectWork?.cancel()
            reconnectWork = nil
            scanTimer?.invalidate()
            scanTimer = nil
            isScanning = false
            tearDownLink()
            targetPeripheral = nil
            requestedDisconnects.removeAll()
            discoveredDiffusers = []
        }
    }

    public func centralManager(_ central: CBCentralManager, didDiscover peripheral: CBPeripheral, advertisementData: [String : Any], rssi RSSI: NSNumber) {
        let rawName = peripheral.name ?? (advertisementData[CBAdvertisementDataLocalNameKey] as? String) ?? ""
        let serviceUUIDs = advertisementData[CBAdvertisementDataServiceUUIDsKey] as? [CBUUID] ?? []

        let isMatch = DiffuserProtocol.isRecognisedDeviceName(rawName) ||
                      serviceUUIDs.contains(Self.serviceCBUUID) ||
                      (knownAddress != nil && peripheral.identifier.uuidString == knownAddress)
        guard isMatch else { return }

        noteDiscovered(peripheral)

        // Only adopt a diffuser when none is chosen. Retargeting while a connect
        // to the chosen one was still pending left two connects outstanding and,
        // with two diffusers in range, both ended up connected.
        guard targetPeripheral == nil else { return }
        targetPeripheral = peripheral
        peripheralName = rawName.isEmpty ? "Diffuser" : rawName
        central.connect(peripheral, options: nil)
    }

    public func centralManager(_ central: CBCentralManager, didConnect peripheral: CBPeripheral) {
        guard peripheral.identifier == targetPeripheral?.identifier else {
            // A connect that outlived the user's choice. Never hold two links.
            requestedDisconnects.insert(peripheral.identifier)
            central.cancelPeripheralConnection(peripheral)
            return
        }

        reconnectWork?.cancel()
        reconnectWork = nil
        receivedPacket = false
        sawPairingError = false
        isConnected = true
        lastSprayTime = Date()
        // A reconnect scan has done its job. A scan the user asked for keeps
        // running so other diffusers can still be listed.
        if scanPurpose == .reconnect {
            stopScanning()
        }

        let address = peripheral.identifier.uuidString
        knownAddress = address
        AppConfig.update { $0.lastAddress = address }

        peripheral.delegate = self
        peripheral.discoverServices([Self.serviceCBUUID])
    }

    public func centralManager(_ central: CBCentralManager, didFailToConnect peripheral: CBPeripheral, error: Error?) {
        let requested = requestedDisconnects.remove(peripheral.identifier) != nil
        guard peripheral.identifier == targetPeripheral?.identifier else { return }
        tearDownLink()
        guard !requested else { return }
        linkEnded(error: error, receivedPacket: false, sawPairingError: false)
    }

    public func centralManager(_ central: CBCentralManager, didDisconnectPeripheral peripheral: CBPeripheral, error: Error?) {
        let requested = requestedDisconnects.remove(peripheral.identifier) != nil
        // Another peripheral's link ending says nothing about ours. Acting on it
        // tore down the live connection and reconnected to the old diffuser.
        guard peripheral.identifier == targetPeripheral?.identifier else { return }

        let hadPacket = receivedPacket
        let hadPairingError = sawPairingError
        tearDownLink()
        guard !requested else { return }
        linkEnded(error: error, receivedPacket: hadPacket, sawPairingError: hadPairingError)
    }

    // MARK: - CBPeripheralDelegate

    private func isTarget(_ peripheral: CBPeripheral) -> Bool {
        peripheral.identifier == targetPeripheral?.identifier
    }

    private func noteGATTError(_ error: Error?) {
        if ReconnectPolicy.isPairingFailure(error) {
            sawPairingError = true
            needsPairing = true
        }
    }

    public func peripheral(_ peripheral: CBPeripheral, didDiscoverServices error: Error?) {
        guard isTarget(peripheral) else { return }
        noteGATTError(error)
        guard let services = peripheral.services else { return }
        for service in services where service.uuid == Self.serviceCBUUID {
            peripheral.discoverCharacteristics([Self.commandCBUUID, Self.notifyCBUUID], for: service)
        }
    }

    public func peripheral(_ peripheral: CBPeripheral, didDiscoverCharacteristicsFor service: CBService, error: Error?) {
        guard isTarget(peripheral) else { return }
        noteGATTError(error)
        guard let characteristics = service.characteristics else { return }
        for char in characteristics where char.uuid == Self.commandCBUUID {
            commandCharacteristic = char
        }
        for char in characteristics where char.uuid == Self.notifyCBUUID {
            peripheral.setNotifyValue(true, for: char)
        }
    }

    public func peripheral(_ peripheral: CBPeripheral, didUpdateNotificationStateFor characteristic: CBCharacteristic, error: Error?) {
        guard isTarget(peripheral), characteristic.uuid == Self.notifyCBUUID else { return }
        if let error {
            noteGATTError(error)
            return
        }
        if characteristic.isNotifying && commandCharacteristic != nil {
            linkBecameReady()
        }
    }

    public func peripheral(_ peripheral: CBPeripheral, didUpdateValueFor characteristic: CBCharacteristic, error: Error?) {
        guard isTarget(peripheral) else { return }
        noteGATTError(error)
        guard let data = characteristic.value,
              let packet = DiffuserProtocol.decodePacket(data) else { return }
        // The delegate queue is the main queue, so this runs in order with
        // everything else. No extra hop that could land after a disconnect.
        handle(cmd: packet.cmd, payload: packet.payload)
    }

    private func handle(cmd: UInt8, payload: [UInt8]) {
        // A decoded packet is the only proof the link is usable. Connecting
        // alone succeeds even when pairing is about to fail.
        receivedPacket = true
        sawPairingError = false
        packetCount &+= 1
        policy.reset()
        needsPairing = false

        switch cmd {
        case DiffuserProtocol.cmdQueryStatus:
            guard payload.count >= 4 else { break }
            isPoweredOn = payload[0] == 1
            isSpraying = payload[1] == 1
            if (0...4).contains(Int(payload[2])) {
                currentIntensity = Int(payload[2])
            }
            if (0...4).contains(Int(payload[3])) {
                batteryLevel = Int(payload[3])
            }
            if payload.count >= 6, (0...26).contains(Int(payload[5])) {
                scheduleInfo = Int(payload[5])
            }
            let isFirstStatus = !hasDeviceState
            hasDeviceState = true
            if isFirstStatus {
                // Load the schedules once per connection, not only when the
                // Schedule window opens, so diagnostics and the "current
                // schedule" have something to refer to. Slightly delayed so
                // the batch does not pile onto the connect-time queries.
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
                    guard let self, self.isReady, !self.isLoadingSchedules, self.schedules.isEmpty else { return }
                    self.refreshSchedules()
                }
            }
            if let wanted = powerAwaitingConfirmation {
                // The replayed power command went out before the link was proven.
                // If the diffuser's own report disagrees, it never arrived.
                powerAwaitingConfirmation = nil
                if isPoweredOn != wanted {
                    setPower(on: wanted)
                }
            }
        case DiffuserProtocol.cmdQuerySchedule, DiffuserProtocol.cmdSetSchedule:
            if let schedule = DiffuserProtocol.decodeSchedule(payload: payload) {
                schedules[schedule.slot] = schedule
                scheduleLoad?.didAnswer(slot: schedule.slot)
            } else if cmd == DiffuserProtocol.cmdQuerySchedule,
                      let first = payload.first, first & 0x0F == 0,
                      let slot = scheduleLoad?.didAnswerWithoutSlot() {
                // An answer that names no slot: the reply for an empty one.
                schedules.removeValue(forKey: slot)
            }
            finishScheduleLoadIfComplete()
        case DiffuserProtocol.cmdSprayReport:
            guard payload.count >= 1 else { break }
            let spraying = payload[0] == 1
            // The one place a puff is counted: the diffuser saying a spray began,
            // whether it was scheduled, periodic or a boost we asked for.
            if spraying && !lastReportedSpraying {
                AppConfig.update { $0.totalPuffsCount += 1 }
            }
            lastReportedSpraying = spraying
            isSpraying = spraying
        case DiffuserProtocol.cmdBatteryReport:
            if payload.count >= 1,
               DiffuserProtocol.shouldAcceptBatteryLevel(Int(payload[0]), previous: batteryLevel) {
                batteryLevel = Int(payload[0])
            }
        case DiffuserProtocol.cmdSetPower:
            if payload.count >= 1 {
                isPoweredOn = payload[0] == 1
            }
        case DiffuserProtocol.cmdSetIntensity:
            if payload.count >= 1, (0...4).contains(Int(payload[0])) {
                currentIntensity = Int(payload[0])
            }
        case DiffuserProtocol.cmdSetRoomFragrance:
            guard payload.count >= 2 else { break }
            // The echo is not trustworthy for every id (the original app's decoder only
            // acknowledges fragrances 1-31), so anything outside the tables is
            // ignored rather than replacing what the user chose.
            if DiffuserProtocol.isValidRoomId(Int(payload[0])) {
                roomId = Int(payload[0])
            }
            if DiffuserProtocol.isValidFragranceId(Int(payload[1])) {
                fragranceId = Int(payload[1])
            }
            persistRoomAndFragrance()
        case DiffuserProtocol.cmdQueryMcuFirmware:
            if payload.count >= 4 {
                mcuFirmware = "\(payload[0]).\(String(format: "%02d", payload[1])),\(payload[2]).\(String(format: "%02d", payload[3]))"
            }
        case DiffuserProtocol.cmdQueryBleFirmware:
            if payload.count >= 4 {
                bleFirmware = "\(payload[0]).\(String(format: "%02d", payload[1])),\(payload[2]).\(String(format: "%02d", payload[3]))"
            }
        default:
            break
        }
    }
}
