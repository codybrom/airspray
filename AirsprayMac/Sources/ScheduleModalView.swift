import SwiftUI
import AppKit

public final class ScheduleWindowController: NSWindowController {
    public static let shared = ScheduleWindowController()

    private init() {
        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 460, height: 380),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.center()
        window.isReleasedWhenClosed = false
        super.init(window: window)
    }

    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    public func showModal() {
        NSApp.activate(ignoringOtherApps: true)
        window?.title = "Schedules"
        window?.contentView = NSHostingView(rootView: ScheduleListView())
        window?.center()
        window?.makeKeyAndOrderFront(nil)
    }
}

// MARK: - List

public struct ScheduleListView: View {
    @ObservedObject var diffuser = DiffuserManager.shared
    @State private var editing: DeviceSchedule?

    public init() {}

    private var configured: [DeviceSchedule] {
        diffuser.schedules.values.filter(\.isConfigured).sorted { $0.slot < $1.slot }
    }

    /// Names where the diffuser is, and makes clear these live on the hardware
    /// rather than being driven by the Mac. Room comes from config, which is
    /// single-diffuser today -- it is not yet keyed by device address.
    private var subtitle: String {
        let base = "Stored on the diffuser"
        guard let room = DiffuserProtocol.roomNames[diffuser.roomId] else { return base }
        return "\(base) · \(room)"
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text(subtitle)
                .font(.caption)
                .foregroundColor(.secondary)

            if diffuser.isLoadingSchedules {
                loading
            } else if diffuser.scheduleLoadFailed {
                failed
            } else {
                list
            }

            Spacer(minLength: 0)
            Divider()

            HStack {
                Text("\(configured.count) of \(DiffuserManager.scheduleSlotCount)")
                    .font(.caption)
                    .foregroundColor(.secondary)
                Spacer()
                Button("Done") { ScheduleWindowController.shared.close() }
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(18)
        .frame(width: 460, height: 380)
        .onAppear { diffuser.refreshSchedules() }
        .sheet(item: $editing) { schedule in
            ScheduleEditorView(schedule: schedule) { editing = nil }
        }
    }

    private var loading: some View {
        VStack(spacing: 10) {
            ProgressView()
            Text("Reading schedules from diffuser…")
                .font(.subheadline)
                .foregroundColor(.secondary)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    private var failed: some View {
        VStack(spacing: 10) {
            Text(diffuser.isConnected
                 ? "Couldn't read schedules from the diffuser."
                 : "Not connected to a diffuser.")
                .font(.subheadline)
                .foregroundColor(.secondary)
            Button("Retry") { diffuser.refreshSchedules() }
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    @ViewBuilder
    private var list: some View {
        if configured.isEmpty {
            VStack(spacing: 10) {
                Text("No schedules on this diffuser.")
                    .font(.subheadline)
                    .foregroundColor(.secondary)
                addButton
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollView {
                VStack(spacing: 6) {
                    ForEach(configured, id: \.slot) { schedule in
                        row(for: schedule)
                    }
                }
            }
            addButton
        }
    }

    private func row(for schedule: DeviceSchedule) -> some View {
        Button(action: { editing = schedule }) {
            HStack {
                VStack(alignment: .leading, spacing: 2) {
                    Text(ScheduleFormat.window(schedule))
                        .font(.system(size: 13, weight: .medium))
                        .foregroundColor(schedule.enabled ? .primary : .secondary)
                    Text("\(schedule.daysDescription) · \(DiffuserProtocol.intensityNames[schedule.intensity] ?? "—")"
                         + (schedule.enabled ? "" : " · Off"))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }
                Spacer()
                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(10)
            .background(Color.primary.opacity(0.04))
            .cornerRadius(8)
        }
        .buttonStyle(.plain)
    }

    private var addButton: some View {
        Button(action: {
            if let slot = diffuser.firstFreeScheduleSlot {
                editing = DeviceSchedule(slot: slot)
            }
        }) {
            Label("Add Schedule", systemImage: "plus")
                .font(.subheadline)
        }
        .buttonStyle(.plain)
        .foregroundColor(.accentColor)
        .disabled(diffuser.firstFreeScheduleSlot == nil)
    }
}

// MARK: - Editor

public struct ScheduleEditorView: View {
    @ObservedObject var diffuser = DiffuserManager.shared

    @State private var days: Set<Int>
    @State private var startTime: Date
    @State private var endTime: Date
    @State private var intensity: Int
    @State private var enabled: Bool

    private let slot: Int
    private let isExisting: Bool
    private let dismiss: () -> Void

    private let dayOrder = [1, 2, 3, 4, 5, 6, 7]

    public init(schedule: DeviceSchedule, dismiss: @escaping () -> Void) {
        self.slot = schedule.slot
        self.dismiss = dismiss
        self.isExisting = DiffuserManager.shared.schedules[schedule.slot]?.isConfigured == true
        _days = State(initialValue: schedule.days.isEmpty ? [1, 2, 3, 4, 5] : schedule.days)
        _startTime = State(initialValue: ScheduleFormat.date(hour: schedule.startHour, minute: schedule.startMin))
        _endTime = State(initialValue: ScheduleFormat.date(hour: schedule.endHour, minute: schedule.endMin))
        _intensity = State(initialValue: (1...4).contains(schedule.intensity) ? schedule.intensity : 2)
        _enabled = State(initialValue: schedule.enabled || !(DiffuserManager.shared.schedules[schedule.slot]?.isConfigured ?? false))
    }

    private var dailyHours: Double {
        let cal = Calendar.current
        let start = cal.component(.hour, from: startTime) * 60 + cal.component(.minute, from: startTime)
        let end = cal.component(.hour, from: endTime) * 60 + cal.component(.minute, from: endTime)
        var minutes = end - start
        if minutes <= 0 { minutes += 24 * 60 }
        return Double(minutes) / 60.0
    }

    public var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            VStack(alignment: .leading, spacing: 14) {
                // Days
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("Active Days")
                            .font(.subheadline)
                            .fontWeight(.medium)
                        Spacer()
                        Button("Weekdays") { days = [1, 2, 3, 4, 5] }
                            .buttonStyle(.plain).foregroundColor(.accentColor).font(.caption)
                        Text("·").foregroundColor(.secondary).font(.caption)
                        Button("Every day") { days = Set(dayOrder) }
                            .buttonStyle(.plain).foregroundColor(.accentColor).font(.caption)
                    }

                    HStack(spacing: 6) {
                        ForEach(dayOrder, id: \.self) { day in
                            let isOn = days.contains(day)
                            Button(action: {
                                if isOn { if days.count > 1 { days.remove(day) } } else { days.insert(day) }
                            }) {
                                Text(DeviceSchedule.dayAbbreviations[day] ?? "")
                                    .font(.system(size: 11, weight: isOn ? .semibold : .regular))
                                    .frame(maxWidth: .infinity, minHeight: 26)
                                    .background(isOn ? Color.accentColor : Color.primary.opacity(0.06))
                                    .foregroundColor(isOn ? .white : .primary)
                                    .cornerRadius(6)
                            }
                            .buttonStyle(.plain)
                        }
                    }
                }

                Divider()

                // Window. The pickers get their own row so "to" is never squeezed.
                VStack(alignment: .leading, spacing: 8) {
                    Text("Operating Window")
                        .font(.subheadline)
                        .fontWeight(.medium)
                    HStack(spacing: 14) {
                        DatePicker("", selection: $startTime, displayedComponents: .hourAndMinute)
                            .labelsHidden()
                        Text("to").foregroundColor(.secondary)
                        DatePicker("", selection: $endTime, displayedComponents: .hourAndMinute)
                            .labelsHidden()
                        Spacer()
                    }
                    Text(String(format: "%.1f hrs/day · %.1f hrs/wk", dailyHours, dailyHours * Double(days.count)))
                        .font(.caption)
                        .foregroundColor(.secondary)
                }

                Divider()

                HStack {
                    Text("Intensity")
                        .font(.subheadline)
                        .fontWeight(.medium)
                    Spacer()
                    Picker("", selection: $intensity) {
                        ForEach(1...4, id: \.self) { level in
                            Text(DiffuserProtocol.intensityNames[level] ?? "").tag(level)
                        }
                    }
                    .labelsHidden()
                    .frame(width: 130)
                }

                Toggle("Enabled", isOn: $enabled)
                    .font(.subheadline)
            }
            .padding(14)
            .background(Color.primary.opacity(0.04))
            .cornerRadius(10)

            Spacer(minLength: 0)

            HStack {
                if isExisting {
                    Button("Delete") {
                        diffuser.deleteSchedule(slot: slot)
                        dismiss()
                    }
                    .buttonStyle(.plain)
                    .foregroundColor(.red)
                    .font(.subheadline)
                }
                Spacer()
                Button("Cancel") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("Save to Diffuser") { save() }
                    .keyboardShortcut(.defaultAction)
                    .buttonStyle(.borderedProminent)
            }
        }
        .padding(18)
        .frame(width: 440, height: 400)
    }

    private func save() {
        let cal = Calendar.current
        let schedule = DeviceSchedule(
            slot: slot,
            nameId: 0,
            enabled: enabled,
            startHour: cal.component(.hour, from: startTime),
            startMin: cal.component(.minute, from: startTime),
            endHour: cal.component(.hour, from: endTime),
            endMin: cal.component(.minute, from: endTime),
            days: days,
            repeatsWeekly: true,
            intensity: intensity
        )
        diffuser.saveSchedule(schedule)
        dismiss()
    }
}

// MARK: - Formatting helpers

public enum ScheduleFormat {
    public static func date(hour: Int, minute: Int) -> Date {
        Calendar.current.date(from: DateComponents(hour: hour, minute: minute)) ?? Date()
    }

    private static let timeFormatter: DateFormatter = {
        let f = DateFormatter()
        f.timeStyle = .short
        f.dateStyle = .none
        return f
    }()

    public static func window(_ schedule: DeviceSchedule) -> String {
        let start = timeFormatter.string(from: date(hour: schedule.startHour, minute: schedule.startMin))
        let end = timeFormatter.string(from: date(hour: schedule.endHour, minute: schedule.endMin))
        return "\(start) – \(end)"
    }
}

extension DeviceSchedule: Identifiable {
    public var id: Int { slot }
}
