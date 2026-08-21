import SwiftUI
import AppKit

public final class RefillWindowController: NSWindowController {
    public static let shared = RefillWindowController()

    private init() {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 420, height: 300),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "Refill Bottle"
        window.center()
        window.isReleasedWhenClosed = false
        super.init(window: window)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    public func showModal() {
        NSApp.activate(ignoringOtherApps: true)
        // Rebuild the view so it opens showing the stored values, not whatever
        // was left staged the last time the window was dismissed.
        window?.contentView = NSHostingView(rootView: RefillModalView())
        window?.center()
        window?.makeKeyAndOrderFront(nil)
    }
}

public struct RefillModalView: View {
    @ObservedObject var diffuser = DiffuserManager.shared

    @State private var percent: Double
    @State private var fragranceId: Int

    /// Quick amounts. Nothing is written until Save, so these only move the slider.
    private let quickAmounts: [(label: String, value: Double)] = [
        ("New", 100), ("¾", 75), ("Half", 50), ("¼", 25)
    ]

    private static let percentFormatter: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .none
        f.minimum = 0
        f.maximum = 100
        f.maximumFractionDigits = 0
        return f
    }()

    public init() {
        let config = AppConfig.load()
        _percent = State(initialValue: RefillDepletionModel.clampPercentage(config.refillPercentage))
        _fragranceId = State(initialValue: config.lastFragranceId)
    }

    /// Forecast for the staged value, so the projection tracks the slider.
    private var forecast: (daysText: String, dateText: String, hoursText: String) {
        RefillDepletionModel.formatDepletionForecast(
            percent: percent,
            intensity: diffuser.currentIntensity,
            dailyHours: 8.0
        )
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 14) {
                // Level
                VStack(alignment: .leading, spacing: 10) {
                    Text("Level")
                        .font(.subheadline)
                        .fontWeight(.medium)

                    HStack(spacing: 6) {
                        ForEach(quickAmounts, id: \.label) { amount in
                            let isSelected = Int(percent.rounded()) == Int(amount.value)
                            Button(action: { percent = amount.value }) {
                                Text(amount.label)
                                    .font(.system(size: 11, weight: isSelected ? .semibold : .regular))
                                    .frame(maxWidth: .infinity, minHeight: 26)
                                    .background(isSelected ? Color.accentColor : Color.primary.opacity(0.06))
                                    .foregroundColor(isSelected ? .white : .primary)
                                    .cornerRadius(6)
                            }
                            .buttonStyle(.plain)
                        }
                    }

                    HStack(spacing: 12) {
                        Slider(value: $percent, in: 0...100, step: 1)
                        TextField("", value: $percent, formatter: Self.percentFormatter)
                            .textFieldStyle(.roundedBorder)
                            .multilineTextAlignment(.trailing)
                            .frame(width: 52)
                        Text("%")
                            .foregroundColor(.secondary)
                    }

                    Text("~\(forecast.daysText) · empty around \(forecast.dateText)")
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Divider()

                // Fragrance
                VStack(alignment: .leading, spacing: 8) {
                    Text("Fragrance")
                        .font(.subheadline)
                        .fontWeight(.medium)

                    Picker("", selection: $fragranceId) {
                        ForEach(DiffuserProtocol.fragrancesSortedByName, id: \.id) { entry in
                            Text(entry.name).tag(entry.id)
                        }
                    }
                    .labelsHidden()
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
            }
            .padding(14)
            .background(Color.primary.opacity(0.04))
            .cornerRadius(10)

            Spacer(minLength: 0)

            HStack {
                Spacer()
                Button("Cancel") { RefillWindowController.shared.close() }
                Button("Save") { save() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: 420, height: 300)
    }

    private func save() {
        let clamped = RefillDepletionModel.clampPercentage(percent)

        var config = AppConfig.load()
        config.refillPercentage = clamped
        // A full bottle is a new bottle; restart the clock so the forecast is honest.
        if clamped >= 100.0 {
            config.refillInstalledDate = RefillDepletionModel.installedDateString()
        }
        config.save()

        // Writes room + fragrance to the diffuser and persists the fragrance.
        diffuser.setFragrance(id: fragranceId)

        RefillWindowController.shared.close()
    }
}
