import Cocoa
import CoreBluetooth
import ServiceManagement

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    let diffuser = DiffuserManager.shared

    /// Single source of truth for room IDs, shared with the protocol layer.
    private let roomNames: [Int: String] = DiffuserProtocol.roomNames

    /// Level 0 is labelled so the menu can show that state if the device reports it,
    /// but it is not offered as a choice -- "Pause Diffuser" is the way to pause.
    private static let intensityLabels = DiffuserProtocol.intensityNames

    /// Set when locking the screen is what paused the diffuser, so unlocking
    /// only undoes its own pause and never overrides one the user made by hand.
    private var pausedByScreenLock = false

    private var menuUpdateTimer: Timer?
    private var refreshQueued = false
    private var lastLoginItemError: String?

    // The menu is built once and its items updated in place. Replacing
    // `statusItem.menu` on every device notification swapped the menu out from
    // under an open one, leaving the visible copy frozen.
    private let menu = NSMenu()
    private let connectionItem = NSMenuItem()
    private var pairingHelpItems: [NSMenuItem] = []
    private let pendingPowerItem = NSMenuItem()
    private let batteryItem = NSMenuItem()
    private let refillStatusItem = NSMenuItem()
    private let countdownItem = NSMenuItem()
    private let powerItem = NSMenuItem()
    private let boostItem = NSMenuItem()
    private let intensityItem = NSMenuItem()
    private let intensityMenu = NSMenu()
    private let scheduleItem = NSMenuItem()
    private let refillItem = NSMenuItem()
    private let presenceItem = NSMenuItem()
    private let loginItem = NSMenuItem()
    private let loginHelpItem = NSMenuItem()
    private let roomItem = NSMenuItem()
    private let roomMenu = NSMenu()
    private let scanItem = NSMenuItem()
    private let switchItem = NSMenuItem()
    private let switchMenu = NSMenu()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.accessory)

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)

        buildMenu()
        statusItem.menu = menu
        updateIcon()
        refreshMenu()
        syncLaunchAtLoginConfig()

        diffuser.onStateChange = { [weak self] in
            self?.queueRefresh()
        }

        // Setup lock/unlock observer
        DistributedNotificationCenter.default().addObserver(
            self,
            selector: #selector(screenLocked),
            name: NSNotification.Name("com.apple.screenIsLocked"),
            object: nil
        )
        DistributedNotificationCenter.default().addObserver(
            self,
            selector: #selector(screenUnlocked),
            name: NSNotification.Name("com.apple.screenIsUnlocked"),
            object: nil
        )
        NSWorkspace.shared.notificationCenter.addObserver(
            self,
            selector: #selector(systemDidWake),
            name: NSWorkspace.didWakeNotification,
            object: nil
        )
    }

    func applicationWillTerminate(_ notification: Notification) {
        diffuser.shutdown()
    }

    // MARK: - Lock, unlock and wake

    @objc private func screenLocked() {
        guard AppConfig.load().isDeskPresenceEnabled else { return }
        // Already paused (or a pause is queued): that pause is the user's, and
        // unlocking must leave it alone.
        guard diffuser.effectivePowerOn else { return }
        pausedByScreenLock = true
        diffuser.setPower(on: false)
    }

    @objc private func screenUnlocked() {
        diffuser.reconnectNow()
        guard pausedByScreenLock else { return }
        pausedByScreenLock = false
        if diffuser.pendingPowerOn == false {
            // The pause never reached the diffuser, so there is nothing to undo.
            diffuser.cancelPendingPower()
        } else {
            diffuser.setPower(on: true)
        }
    }

    @objc private func systemDidWake() {
        diffuser.reconnectNow()
    }

    private func sanitizedDeviceName(_ raw: String) -> String {
        var clean = raw
            .replacingOccurrences(of: "Air Wick", with: "Diffuser", options: .caseInsensitive)
            .replacingOccurrences(of: "AirWick", with: "Diffuser", options: .caseInsensitive)
            .trimmingCharacters(in: .whitespaces)
        if clean.isEmpty || clean.lowercased() == "diffuser mist" || clean.lowercased() == "mist" {
            clean = "Diffuser"
        }
        return clean
    }

    private func updateIcon() {
        guard let button = statusItem?.button else { return }
        let isActive = diffuser.isConnected && diffuser.hasDeviceState && diffuser.isPoweredOn
        button.image = IconHelper.makeMenuBarIcon(isPoweredOn: isActive)
    }

    // MARK: - Menu construction
    //
    // The top level holds live state and the controls reached most often. Anything
    // configured once -- hardware discovery, app preferences, diagnostics -- lives
    // one level down so the menu stays short.

    private func configure(_ item: NSMenuItem, title: String = "", action: Selector? = nil, key: String = "") {
        item.title = title
        item.action = action
        item.target = action == nil ? nil : self
        item.keyEquivalent = key
    }

    private func infoItem(_ item: NSMenuItem = NSMenuItem(), title: String = "") -> NSMenuItem {
        configure(item, title: title)
        item.isEnabled = false
        return item
    }

    private func buildMenu() {
        menu.delegate = self
        // Enabled state is set explicitly from the connection state. Automatic
        // validation would re-enable every item that has an action.
        for m in [menu, intensityMenu, roomMenu, switchMenu] {
            m.autoenablesItems = false
        }

        // Read-only live state: connection, battery, refill, next spray.
        menu.addItem(infoItem(connectionItem))
        for line in DiffuserManager.pairingHelpLines {
            let item = infoItem(title: line)
            pairingHelpItems.append(item)
            menu.addItem(item)
        }
        menu.addItem(infoItem(pendingPowerItem))
        menu.addItem(infoItem(batteryItem))
        menu.addItem(infoItem(refillStatusItem))
        menu.addItem(infoItem(countdownItem))
        menu.addItem(NSMenuItem.separator())

        // Live controls
        configure(powerItem, action: #selector(togglePower), key: "p")
        menu.addItem(powerItem)
        configure(boostItem, action: #selector(triggerBoost), key: "b")
        menu.addItem(boostItem)

        // 1...4 only: level 0 is a pause, offered as "Pause Diffuser" instead.
        for lvl in 1...4 {
            let item = NSMenuItem()
            configure(item, action: #selector(setIntensityLevel(_:)))
            item.tag = lvl
            intensityMenu.addItem(item)
        }
        intensityItem.submenu = intensityMenu
        menu.addItem(intensityItem)

        menu.addItem(NSMenuItem.separator())

        // Scheduling and consumables
        configure(scheduleItem, title: "Schedule...", action: #selector(openScheduleModal), key: "k")
        menu.addItem(scheduleItem)
        configure(refillItem, title: "Refill Bottle...", action: #selector(openRefillModal), key: "r")
        menu.addItem(refillItem)

        menu.addItem(NSMenuItem.separator())

        // Everything infrequent lives here, so there is only one place to look.
        let settingsItem = NSMenuItem(title: "Settings", action: nil, keyEquivalent: "")
        settingsItem.submenu = makeSettingsMenu()
        menu.addItem(settingsItem)

        menu.addItem(NSMenuItem.separator())
        // Which build is running, readable without opening anything.
        menu.addItem(infoItem(title: Self.appVersionText))
        let quitItem = NSMenuItem()
        configure(quitItem, title: "Quit AirSpray", action: #selector(quitApp), key: "q")
        menu.addItem(quitItem)
    }

    /// "AirSpray 1.1.0 (2)", read from the bundle so it cannot drift from the
    /// build that is actually running.
    static var appVersionText: String {
        let info = Bundle.main.infoDictionary
        let version = info?["CFBundleShortVersionString"] as? String ?? "?"
        let build = info?["CFBundleVersion"] as? String ?? "?"
        return "AirSpray \(version) (\(build))"
    }

    /// Everything you set once or reach for only when something is wrong:
    /// app preferences, then discovery, then support output.
    private func makeSettingsMenu() -> NSMenu {
        let settingsMenu = NSMenu()
        settingsMenu.autoenablesItems = false

        configure(presenceItem, title: "Auto-Pause on Screen Lock", action: #selector(toggleDeskPresence))
        settingsMenu.addItem(presenceItem)

        configure(loginItem, title: "Launch at Login", action: #selector(toggleLaunchAtLogin))
        settingsMenu.addItem(loginItem)
        configure(loginHelpItem, action: #selector(openLoginItemsSettings))
        settingsMenu.addItem(loginHelpItem)

        for (id, name) in roomNames.sorted(by: { $0.key < $1.key }) {
            let item = NSMenuItem()
            configure(item, title: name, action: #selector(setRoomLocation(_:)))
            item.tag = id
            roomMenu.addItem(item)
        }
        roomItem.submenu = roomMenu
        settingsMenu.addItem(roomItem)

        settingsMenu.addItem(NSMenuItem.separator())

        configure(scanItem, action: #selector(triggerScanForDiffusers), key: "s")
        settingsMenu.addItem(scanItem)

        configure(switchItem, title: "Switch Diffuser")
        switchItem.submenu = switchMenu
        settingsMenu.addItem(switchItem)

        settingsMenu.addItem(NSMenuItem.separator())
        let diagnosticsItem = NSMenuItem()
        configure(diagnosticsItem, title: "Copy Diagnostics & Schema", action: #selector(copyDiagnostics))
        settingsMenu.addItem(diagnosticsItem)
        let aboutItem = NSMenuItem()
        configure(aboutItem, title: "About AirSpray", action: #selector(showAbout))
        settingsMenu.addItem(aboutItem)

        return settingsMenu
    }

    // MARK: - Menu state

    /// Several published values usually change together. One refresh covers them.
    private func queueRefresh() {
        guard !refreshQueued else { return }
        refreshQueued = true
        DispatchQueue.main.async { [weak self] in
            guard let self else { return }
            self.refreshQueued = false
            self.updateIcon()
            self.refreshMenu()
        }
    }

    private var connectionTitle: String {
        if diffuser.isConnected {
            let name = sanitizedDeviceName(diffuser.peripheralName)
            if diffuser.needsPairing { return "Needs Pairing (\(name))" }
            return diffuser.hasDeviceState ? "Connected (\(name))" : "Connected (\(name)), Reading State..."
        }
        if diffuser.needsPairing { return "Diffuser Needs Pairing" }
        if diffuser.isScanning { return "Scanning for Diffusers..." }
        return "Disconnected"
    }

    /// Update every item from current state. The config is read here, not kept:
    /// a copy loaded at launch showed the launch-time refill level forever.
    private func refreshMenu() {
        let config = AppConfig.load()
        let ready = diffuser.isReady
        let known = diffuser.isConnected && diffuser.hasDeviceState

        connectionItem.title = connectionTitle
        for item in pairingHelpItems {
            item.isHidden = !diffuser.needsPairing
        }

        if let pending = diffuser.pendingPowerOn {
            pendingPowerItem.title = pending
                ? "Resume pending. Will send when the diffuser connects"
                : "Pause pending. Will send when the diffuser connects"
            pendingPowerItem.isHidden = false
        } else {
            pendingPowerItem.isHidden = true
        }

        batteryItem.title = "Battery: \(DiffuserProtocol.batteryDescription(diffuser.batteryLevel))"
        batteryItem.isHidden = !known

        let intensity = diffuser.currentIntensity
        refillStatusItem.title = "Refill: " + RefillDepletionModel.headerSummary(
            percent: config.refillPercentage, intensity: known ? intensity : config.defaultIntensity, dailyHours: 8.0)
        refillStatusItem.isHidden = !diffuser.isConnected

        countdownItem.title = diffuser.nextSprayCountdownString
        countdownItem.isHidden = !known

        // Pause/Resume may be queued while disconnected, so it stays enabled.
        powerItem.title = diffuser.effectivePowerOn ? "Pause Diffuser" : "Resume Diffuser"

        boostItem.title = diffuser.isSpraying ? "Spraying Fragrance Boost..." : "Spray Fragrance Boost (10m)"
        boostItem.isEnabled = ready

        if known {
            let label = Self.intensityLabels[intensity] ?? "Level \(intensity)"
            intensityItem.title = "Intensity: \(label)"
        } else {
            intensityItem.title = "Intensity"
        }
        intensityItem.isEnabled = ready
        for item in intensityMenu.items {
            let lvl = item.tag
            let label = Self.intensityLabels[lvl] ?? "Level \(lvl)"
            let fc = RefillDepletionModel.formatDepletionForecast(
                percent: config.refillPercentage, intensity: lvl, dailyHours: 8.0)
            if let interval = DiffuserManager.sprayInterval(forIntensity: lvl) {
                item.title = "\(label): every \(Int(interval / 60))m (~\(fc.daysText))"
            } else {
                item.title = "\(label) (~\(fc.daysText))"
            }
            item.state = (known && lvl == intensity) ? .on : .off
            item.isEnabled = ready
        }

        scheduleItem.isEnabled = ready

        // Settings
        presenceItem.state = config.isDeskPresenceEnabled ? .on : .off
        refreshLoginItems()

        roomItem.title = "Room Profile: \(roomNames[diffuser.roomId] ?? "Other")"
        roomItem.isEnabled = ready
        for item in roomMenu.items {
            item.state = (item.tag == diffuser.roomId) ? .on : .off
            item.isEnabled = ready
        }

        scanItem.title = diffuser.isScanning ? "Scanning..." : "Scan for Diffusers..."
        scanItem.isEnabled = !diffuser.isScanning

        refreshSwitchMenu()
    }

    private func refreshLoginItems() {
        let status = SMAppService.mainApp.status
        switch status {
        case .enabled: loginItem.state = .on
        case .requiresApproval: loginItem.state = .mixed
        default: loginItem.state = .off
        }

        if status == .requiresApproval {
            loginHelpItem.title = "Needs approval in System Settings > General > Login Items..."
            loginHelpItem.isHidden = false
        } else if let error = lastLoginItemError {
            loginHelpItem.title = "Launch at Login failed: \(error)"
            loginHelpItem.isHidden = false
        } else {
            loginHelpItem.isHidden = true
        }
    }

    /// Offered whenever there is a diffuser to move to: a second one, or one
    /// found while the chosen one is not answering.
    private func refreshSwitchMenu() {
        let found = diffuser.discoveredDiffusers
        let targetId = diffuser.targetPeripheral?.identifier
        switchItem.isHidden = !(found.count >= 2 || found.contains { $0.identifier != targetId })

        switchMenu.removeAllItems()
        for (idx, p) in found.enumerated() {
            let item = NSMenuItem()
            configure(item, title: sanitizedDeviceName(p.name ?? "Diffuser #\(idx + 1)"),
                      action: #selector(selectDiffuserFromMenu(_:)))
            item.representedObject = p
            item.state = (p.identifier == targetId) ? .on : .off
            switchMenu.addItem(item)
        }
    }

    // MARK: - NSMenuDelegate Live Countdown Updating

    func menuWillOpen(_ menu: NSMenu) {
        refreshMenu()
        menuUpdateTimer?.invalidate()
        // Menu tracking runs the run loop in event-tracking mode, where a timer
        // scheduled in the default mode never fires.
        let timer = Timer(timeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.countdownItem.title = self.diffuser.nextSprayCountdownString
        }
        RunLoop.main.add(timer, forMode: .common)
        menuUpdateTimer = timer
    }

    func menuDidClose(_ menu: NSMenu) {
        menuUpdateTimer?.invalidate()
        menuUpdateTimer = nil
    }

    // MARK: - Actions

    @objc private func togglePower() {
        // A manual choice supersedes whatever the screen lock did.
        pausedByScreenLock = false
        diffuser.setPower(on: !diffuser.effectivePowerOn)
    }

    @objc private func triggerBoost() {
        diffuser.sendBoost()
    }

    @objc private func setIntensityLevel(_ sender: NSMenuItem) {
        diffuser.setIntensity(level: sender.tag)
    }

    @objc private func setRoomLocation(_ sender: NSMenuItem) {
        diffuser.setRoom(id: sender.tag)
    }

    @objc private func selectDiffuserFromMenu(_ sender: NSMenuItem) {
        guard let peripheral = sender.representedObject as? CBPeripheral else { return }
        diffuser.selectDiffuser(peripheral: peripheral)
    }

    @objc private func triggerScanForDiffusers() {
        diffuser.scanForNewDiffuser()
        refreshMenu()
    }

    @objc private func toggleDeskPresence() {
        AppConfig.update { $0.isDeskPresenceEnabled.toggle() }
        refreshMenu()
    }

    @objc private func openRefillModal() {
        RefillWindowController.shared.showModal()
    }

    @objc private func openScheduleModal() {
        ScheduleWindowController.shared.showModal()
    }

    // MARK: - Launch at login

    /// The shared config mirrors the real login-item status, which the user can
    /// also change in System Settings behind the app's back.
    private func syncLaunchAtLoginConfig() {
        let enabled = SMAppService.mainApp.status == .enabled
        AppConfig.update { $0.isLaunchAtLogin = enabled }
    }

    @objc private func toggleLaunchAtLogin() {
        let service = SMAppService.mainApp
        let wasRegistered = service.status == .enabled || service.status == .requiresApproval
        lastLoginItemError = nil
        do {
            if wasRegistered {
                try service.unregister()
            } else {
                try service.register()
            }
        } catch {
            lastLoginItemError = error.localizedDescription
            showLoginItemAlert(
                title: wasRegistered ? "Couldn't turn off Launch at Login" : "Couldn't turn on Launch at Login",
                detail: error.localizedDescription)
        }
        if lastLoginItemError == nil && service.status == .requiresApproval {
            showLoginItemAlert(
                title: "Launch at Login needs approval",
                detail: "Allow AirSpray in System Settings > General > Login Items.")
        }
        syncLaunchAtLoginConfig()
        refreshMenu()
    }

    private func showLoginItemAlert(title: String, detail: String) {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = detail
        alert.addButton(withTitle: "Open Login Items")
        alert.addButton(withTitle: "OK")
        if alert.runModal() == .alertFirstButtonReturn {
            SMAppService.openSystemSettingsLoginItems()
        }
    }

    @objc private func openLoginItemsSettings() {
        SMAppService.openSystemSettingsLoginItems()
    }

    // MARK: - Diagnostics

    @objc private func copyDiagnostics() {
        let config = AppConfig.load()
        var schema = DiffuserSchema.defaultSchema(connected: diffuser.isConnected)
        // Before the first status packet these are placeholders, not readings.
        // The schema's own defaults are left in place instead.
        if diffuser.hasDeviceState {
            schema.state.power = diffuser.isPoweredOn
            schema.state.spraying = diffuser.isSpraying
            schema.state.intensity = diffuser.currentIntensity
            schema.state.intensityLabel = DiffuserProtocol.intensityNames[diffuser.currentIntensity]
                ?? "Level \(diffuser.currentIntensity)"
            schema.state.batteryLevel = diffuser.batteryLevel
        }
        // Left nil deliberately: the diffuser reports 0-4 bars, and level * 25
        // dressed that up as a precise reading in every bug report.
        schema.state.batteryPercentage = nil
        schema.state.refillPercentage = config.refillPercentage
        schema.state.refillInstalledDate = config.refillInstalledDate
        let scheduleStatus = DiffuserProtocol.scheduleStatus(fromInfo: diffuser.scheduleInfo)
        schema.state.scheduleActive = scheduleStatus.active
        schema.state.currentScheduleId = scheduleStatus.slot
        schema.location.roomId = diffuser.roomId
        schema.location.roomName = roomNames[diffuser.roomId] ?? "Other"
        schema.location.fragranceId = diffuser.fragranceId
        schema.deviceInfo.mcuFirmware = diffuser.mcuFirmware
        schema.deviceInfo.bleFirmware = diffuser.bleFirmware
        schema.schedules = DiffuserSchema.scheduleItems(from: diffuser.schedules)

        if let json = schema.toJsonString() {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(json, forType: .string)
        }
    }

    /// The standard About panel (icon, name, version, build), with the
    /// diffuser's firmware underneath when it has reported it.
    @objc private func showAbout() {
        let style = NSMutableParagraphStyle()
        style.alignment = .center
        let credits = NSAttributedString(
            string: aboutDiffuserLines().joined(separator: "\n"),
            attributes: [
                .font: NSFont.systemFont(ofSize: NSFont.smallSystemFontSize),
                .foregroundColor: NSColor.secondaryLabelColor,
                .paragraphStyle: style,
            ]
        )
        // A menu bar app is never frontmost by default, so the panel would
        // open behind whatever the user was working in.
        NSApp.activate(ignoringOtherApps: true)
        NSApp.orderFrontStandardAboutPanel(options: [.credits: credits])
    }

    private func aboutDiffuserLines() -> [String] {
        guard diffuser.isConnected else { return ["No diffuser connected"] }
        var lines = [sanitizedDeviceName(diffuser.peripheralName)]
        // Firmware arrives a moment after connecting, and only if the
        // diffuser answers the queries.
        lines.append("MCU firmware: \(diffuser.mcuFirmware ?? "not reported")")
        lines.append("Bluetooth firmware: \(diffuser.bleFirmware ?? "not reported")")
        return lines
    }

    @objc private func quitApp() {
        NSApplication.shared.terminate(nil)
    }
}
