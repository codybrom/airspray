import Foundation

public struct RefillDepletionModel {
    /// Total operational hours of a full 20mL refill bottle, per intensity level.
    ///
    /// Level 0 pauses spraying, so it is deliberately absent rather than mapped to
    /// zero hours: nothing is consumed and there is nothing to forecast. Keep this
    /// in step with CONSUMPTION_RATE_PER_SEC in airspray/refill.py -- the two are
    /// the same figures expressed as hours-per-bottle and percent-per-second.
    public static let fullBottleOperatingHours: [Int: Double] = [
        1: 360.0, // Low:    ~45 days @ 8h/day
        2: 240.0, // Medium: ~30 days @ 8h/day
        3: 160.0, // High:   ~20 days @ 8h/day
        4: 120.0, // Max:    ~15 days @ 8h/day
    ]

    /// Percentage of the bottle consumed per second at a given intensity.
    /// Zero for paused, and for any level outside the table.
    public static func consumptionPercentPerSecond(for intensity: Int) -> Double {
        guard let hours = fullBottleOperatingHours[intensity], hours > 0 else { return 0.0 }
        return 100.0 / (hours * 3600.0)
    }

    /// Percentage of the bottle a single manual boost burst uses.
    public static let boostConsumptionPercent: Double = 0.08

    /// The date a bottle went in, written the way the CLI writes it
    /// (INSTALLED_DATE_FORMAT in airspray/refill.py). A full ISO 8601 timestamp
    /// would round-trip through the shared config as a string the CLI cannot read.
    /// Whether saving a level starts a new bottle, which restarts the install
    /// date and the spray count.
    ///
    /// A full level always does. A lower level only does when the user says the
    /// bottle is a different one, because the same dialog is also used to
    /// correct the estimate for the bottle already in the diffuser. Tying the
    /// date to 100% alone left a partly used bottle showing the previous
    /// bottle's install date.
    public static func startsNewBottle(level: Double, markedNew: Bool) -> Bool {
        markedNew || level >= 100.0
    }

    public static func installedDateString(_ date: Date = Date()) -> String {
        let formatter = DateFormatter()
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter.string(from: date)
    }

    /// Hours a full bottle lasts, falling back to Medium so a forecast can still
    /// be shown while paused -- it answers "how long once you resume".
    public static func forecastBottleHours(for intensity: Int) -> Double {
        fullBottleOperatingHours[intensity] ?? fullBottleOperatingHours[2] ?? 240.0
    }

    /// Calculate remaining operating hours from current percentage
    public static func remainingOperatingHours(percent: Double, intensity: Int) -> Double {
        return max(0.0, (percent / 100.0) * forecastBottleHours(for: intensity))
    }

    /// Calculate estimated calendar days remaining based on daily active usage hours
    public static func remainingCalendarDays(percent: Double, intensity: Int, dailyHours: Double = 8.0) -> Int {
        let remHours = remainingOperatingHours(percent: percent, intensity: intensity)
        let effectiveDaily = max(1.0, min(24.0, dailyHours))
        return Int(ceil(remHours / effectiveDaily))
    }

    /// Predict the exact calendar date when the refill bottle will run empty
    public static func estimatedEmptyDate(percent: Double, intensity: Int, dailyHours: Double = 8.0) -> Date {
        let days = Double(remainingCalendarDays(percent: percent, intensity: intensity, dailyHours: dailyHours))
        return Calendar.current.date(byAdding: .day, value: Int(days), to: Date()) ?? Date()
    }

    /// Constrain a refill reading to the 0-100 range the rest of the model assumes.
    public static func clampPercentage(_ percent: Double) -> Double {
        return max(0.0, min(100.0, percent))
    }

    /// One-line refill summary for the menu header: remaining percentage first,
    /// then the depletion forecast, e.g. "96% (~30 days left)".
    public static func headerSummary(percent: Double, intensity: Int, dailyHours: Double = 8.0) -> String {
        let clamped = max(0.0, min(100.0, percent))
        let forecast = formatDepletionForecast(
            percent: clamped, intensity: intensity, dailyHours: dailyHours
        )
        return "\(Int(clamped.rounded()))% (~\(forecast.daysText))"
    }

    /// Return a human-readable depletion forecast summary
    public static func formatDepletionForecast(percent: Double, intensity: Int, dailyHours: Double = 8.0) -> (daysText: String, dateText: String, hoursText: String) {
        let days = remainingCalendarDays(percent: percent, intensity: intensity, dailyHours: dailyHours)
        let hours = Int(remainingOperatingHours(percent: percent, intensity: intensity))
        let emptyDate = estimatedEmptyDate(percent: percent, intensity: intensity, dailyHours: dailyHours)

        let formatter = DateFormatter()
        formatter.dateStyle = .medium
        formatter.timeStyle = .none
        let dateString = formatter.string(from: emptyDate)

        let daysText: String
        if days > 1 {
            daysText = "\(days) days left"
        } else if days == 1 && hours >= 8 {
            daysText = "1 day left"
        } else if hours > 1 {
            daysText = "\(hours) hours left"
        } else {
            daysText = "< 1 hour left"
        }
        let hoursText = "\(hours) operating hours left"
        return (daysText, dateString, hoursText)
    }
}
