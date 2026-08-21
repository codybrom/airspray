import Cocoa
import CoreBluetooth
import ServiceManagement

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    var statusItem: NSStatusItem!
    var config = AppConfig.load()
    let diffuser = DiffuserManager.shared
    private var countdownMenuItem: NSMenuItem?
    private var menuUpdateTimer: Timer?

    /// Single source of truth for room IDs, shared with the protocol layer.
    private let roomNames: [Int: String] = DiffuserProtocol.roomNames

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApplication.shared.setActivationPolicy(.accessory)

        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        
        updateIcon()
        updateMenu()

        diffuser.onStateChange = { [weak self] in
            DispatchQueue.main.async {
                self?.updateIcon()
                self?.updateMenu()
            }
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
    }

    @objc private func screenLocked() {
        if config.isDeskPresenceEnabled {
            diffuser.setPower(on: false)
        }
    }

    @objc private func screenUnlocked() {
        if config.isDeskPresenceEnabled {
            diffuser.setPower(on: true)
        }
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
        let isActive = diffuser.isConnected && diffuser.isPoweredOn
        button.image = IconHelper.makeMenuBarIcon(isPoweredOn: isActive)
    }

    /// Level 0 is labelled so the menu can show that state if the device reports it,
    /// but it is not offered as a choice -- "Pause Diffuser" is the way to pause.
    private static let intensityLabels = DiffuserProtocol.intensityNames

    // MARK: - Menu construction
    //
    // The top level holds live state and the controls reached most often. Anything
    // configured once -- hardware discovery, app preferences, diagnostics -- lives
    // one level down so the menu stays short.

    private func updateMenu() {
        let menu = NSMenu()
        menu.delegate = self

        addStatusHeader(to: menu)
        menu.addItem(NSMenuItem.separator())

        // Live controls
        let powerTitle = diffuser.isPoweredOn ? "Pause Diffuser" : "Resume Diffuser"
        menu.addItem(NSMenuItem(title: powerTitle, action: #selector(togglePower), keyEquivalent: "p"))

        let boostTitle = diffuser.isSpraying ? "Spraying Fragrance Boost..." : "Spray Fragrance Boost (10m)"
        menu.addItem(NSMenuItem(title: boostTitle, action: #selector(triggerBoost), keyEquivalent: "b"))

        let currentIntensity = Self.intensityLabels[diffuser.currentIntensity] ?? "Medium"
        let intensityItem = NSMenuItem(title: "Intensity: \(currentIntensity)", action: nil, keyEquivalent: "")
        intensityItem.submenu = makeIntensityMenu()
        menu.addItem(intensityItem)

        menu.addItem(NSMenuItem.separator())

        // Scheduling and consumables
        menu.addItem(NSMenuItem(title: "Schedule...", action: #selector(openScheduleModal), keyEquivalent: "k"))

        menu.addItem(NSMenuItem(title: "Refill Bottle...", action: #selector(openRefillModal), keyEquivalent: "r"))

        menu.addItem(NSMenuItem.separator())

        // Everything infrequent lives here, so there is only one place to look.
        let settingsItem = NSMenuItem(title: "Settings", action: nil, keyEquivalent: "")
        settingsItem.submenu = makeSettingsMenu()
        menu.addItem(settingsItem)

        menu.addItem(NSMenuItem.separator())
        menu.addItem(NSMenuItem(title: "Quit AirSpray", action: #selector(quitApp), keyEquivalent: "q"))

        statusItem.menu = menu
    }

    /// Read-only live state: connection, battery, refill, next spray.
    private func addStatusHeader(to menu: NSMenu) {
        let statusTitle: String
        if diffuser.isScanning {
            statusTitle = "Scanning for Diffusers..."
        } else if diffuser.isConnected {
            statusTitle = "Connected (\(sanitizedDeviceName(diffuser.peripheralName)))"
        } else {
            statusTitle = "Disconnected"
        }

        let statusMenuItem = NSMenuItem(title: statusTitle, action: nil, keyEquivalent: "")
        statusMenuItem.isEnabled = false
        menu.addItem(statusMenuItem)

        guard diffuser.isConnected else { return }

        let batItem = NSMenuItem(
            title: "Battery: \(DiffuserProtocol.batteryDescription(diffuser.batteryLevel))",
            action: nil, keyEquivalent: "")
        batItem.isEnabled = false
        menu.addItem(batItem)

        let refillSummary = RefillDepletionModel.headerSummary(
            percent: config.refillPercentage,
            intensity: diffuser.currentIntensity,
            dailyHours: 8.0
        )
        let refillHeaderItem = NSMenuItem(title: "Refill: \(refillSummary)", action: nil, keyEquivalent: "")
        refillHeaderItem.isEnabled = false
        menu.addItem(refillHeaderItem)

        let countdownItem = NSMenuItem(title: diffuser.nextSprayCountdownString, action: nil, keyEquivalent: "")
        countdownItem.isEnabled = false
        self.countdownMenuItem = countdownItem
        menu.addItem(countdownItem)
    }

    private func makeIntensityMenu() -> NSMenu {
        let intensityMenu = NSMenu()
        // 1...4 only: level 0 is a pause, offered as "Pause Diffuser" instead.
        for lvl in 1...4 {
            let label = Self.intensityLabels[lvl] ?? "Level \(lvl)"
            let fc = RefillDepletionModel.formatDepletionForecast(
                percent: config.refillPercentage,
                intensity: lvl,
                dailyHours: 8.0
            )
            let itemTitle: String
            if let interval = DiffuserManager.sprayInterval(forIntensity: lvl) {
                itemTitle = "\(label) — Every \(Int(interval / 60))m (~\(fc.daysText))"
            } else {
                itemTitle = "\(label) (~\(fc.daysText))"
            }

            let item = NSMenuItem(title: itemTitle, action: #selector(setIntensityLevel(_:)), keyEquivalent: "")
            item.tag = lvl
            item.state = (lvl == diffuser.currentIntensity) ? .on : .off
            intensityMenu.addItem(item)
        }
        return intensityMenu
    }

    /// Everything you set once or reach for only when something is wrong:
    /// app preferences, then discovery, then support output.
    private func makeSettingsMenu() -> NSMenu {
        let settingsMenu = NSMenu()

        let presenceItem = NSMenuItem(title: "Auto-Pause on Screen Lock", action: #selector(toggleDeskPresence), keyEquivalent: "")
        presenceItem.state = config.isDeskPresenceEnabled ? .on : .off
        settingsMenu.addItem(presenceItem)

        let loginItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLaunchAtLogin), keyEquivalent: "")
        loginItem.state = SMAppService.mainApp.status == .enabled ? .on : .off
        settingsMenu.addItem(loginItem)

        let currentRoom = roomNames[diffuser.roomId] ?? "Other"
        let roomItem = NSMenuItem(title: "Room Profile: \(currentRoom)", action: nil, keyEquivalent: "")
        let roomMenu = NSMenu()
        for (id, name) in roomNames.sorted(by: { $0.key < $1.key }) {
            let item = NSMenuItem(title: name, action: #selector(setRoomLocation(_:)), keyEquivalent: "")
            item.tag = id
            item.state = (id == diffuser.roomId) ? .on : .off
            roomMenu.addItem(item)
        }
        roomItem.submenu = roomMenu
        settingsMenu.addItem(roomItem)

        settingsMenu.addItem(NSMenuItem.separator())

        let scanTitle = diffuser.isScanning ? "Scanning..." : "Scan for Diffusers..."
        let scanItem = NSMenuItem(title: scanTitle, action: #selector(triggerScanForDiffusers), keyEquivalent: "s")
        scanItem.isEnabled = !diffuser.isScanning
        settingsMenu.addItem(scanItem)

        // Only meaningful once there is more than one diffuser to choose between.
        if diffuser.discoveredDiffusers.count >= 2 {
            let switchItem = NSMenuItem(title: "Switch Diffuser", action: nil, keyEquivalent: "")
            let switchMenu = NSMenu()
            for (idx, p) in diffuser.discoveredDiffusers.enumerated() {
                let devName = sanitizedDeviceName(p.name ?? "Diffuser #\(idx + 1)")
                let item = NSMenuItem(title: devName, action: #selector(selectDiffuserFromMenu(_:)), keyEquivalent: "")
                item.tag = idx
                item.state = (p.identifier == diffuser.targetPeripheral?.identifier) ? .on : .off
                switchMenu.addItem(item)
            }
            switchItem.submenu = switchMenu
            settingsMenu.addItem(switchItem)
        }

        settingsMenu.addItem(NSMenuItem.separator())
        settingsMenu.addItem(NSMenuItem(title: "Copy Diagnostics & Schema", action: #selector(copyDiagnostics), keyEquivalent: ""))

        return settingsMenu
    }

    // MARK: - NSMenuDelegate Live Countdown Updating

    func menuWillOpen(_ menu: NSMenu) {
        countdownMenuItem?.title = diffuser.nextSprayCountdownString
        menuUpdateTimer?.invalidate()
        menuUpdateTimer = Timer.scheduledTimer(withTimeInterval: 1.0, repeats: true) { [weak self] _ in
            guard let self = self else { return }
            self.countdownMenuItem?.title = self.diffuser.nextSprayCountdownString
        }
    }

    func menuDidClose(_ menu: NSMenu) {
        menuUpdateTimer?.invalidate()
        menuUpdateTimer = nil
    }

    @objc private func togglePower() {
        diffuser.setPower(on: !diffuser.isPoweredOn)
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
        if sender.tag < diffuser.discoveredDiffusers.count {
            diffuser.selectDiffuser(peripheral: diffuser.discoveredDiffusers[sender.tag])
        }
    }

    @objc private func triggerScanForDiffusers() {
        diffuser.scanForNewDiffuser()
        updateMenu()
    }

    @objc private func toggleDeskPresence() {
        config.isDeskPresenceEnabled.toggle()
        config.save()
        updateMenu()
    }

    @objc private func openRefillModal() {
        RefillWindowController.shared.showModal()
    }

    @objc private func openScheduleModal() {
        ScheduleWindowController.shared.showModal()
    }
    
    @objc private func toggleLaunchAtLogin() {
        if SMAppService.mainApp.status == .enabled {
            try? SMAppService.mainApp.unregister()
        } else {
            try? SMAppService.mainApp.register()
        }
        updateMenu()
    }

    @objc private func copyDiagnostics() {
        var schema = DiffuserSchema.defaultSchema(connected: diffuser.isConnected)
        schema.state.power = diffuser.isPoweredOn
        schema.state.spraying = diffuser.isSpraying
        schema.state.intensity = diffuser.currentIntensity
        schema.state.intensityLabel = DiffuserProtocol.intensityNames[diffuser.currentIntensity]
            ?? "Level \(diffuser.currentIntensity)"
        schema.state.batteryLevel = diffuser.batteryLevel
        // Left nil deliberately: the diffuser reports 0-4 bars, and level * 25
        // dressed that up as a precise reading in every bug report.
        schema.state.batteryPercentage = nil
        schema.state.refillPercentage = config.refillPercentage
        schema.location.roomId = diffuser.roomId
        schema.location.roomName = roomNames[diffuser.roomId] ?? "Other"
        schema.deviceInfo.mcuFirmware = diffuser.mcuFirmware
        schema.deviceInfo.bleFirmware = diffuser.bleFirmware

        if let json = schema.toJsonString() {
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(json, forType: .string)
        }
    }

    @objc private func quitApp() {
        NSApplication.shared.terminate(nil)
    }
}
