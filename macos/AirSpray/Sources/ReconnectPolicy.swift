import Foundation

/// Decides how long to wait before reconnecting to the diffuser.
///
/// A diffuser that wants pairing drops the link when the macOS pairing dialog
/// goes unanswered, and every reconnect raises the dialog again. Retrying those
/// on a fixed short delay turns into a prompt every few seconds, so connections
/// that never become usable back off, up to five minutes. A link that had been
/// carrying data and then dropped is ordinary radio flakiness and retries
/// promptly, every time.
///
/// Pure value type with no CoreBluetooth dependency so the policy can be tested.
public struct ReconnectPolicy: Equatable {
    public enum Outcome: Equatable {
        /// The link had delivered at least one decoded packet before it ended.
        case workingLinkDropped
        /// The connection failed, hit a pairing-type error, or ended without
        /// ever delivering a decoded packet.
        case neverUsable
    }

    public static let baseDelay: TimeInterval = 3.0
    public static let maxDelay: TimeInterval = 300.0
    /// Pause before looking again after a scan found nothing to connect to.
    public static let idleRescanDelay: TimeInterval = 30.0

    /// Consecutive connections that never became usable.
    public private(set) var failures = 0

    public init() {}

    public static func delay(afterFailures failures: Int) -> TimeInterval {
        min(baseDelay * pow(2.0, Double(max(0, min(failures, 10)))), maxDelay)
    }

    /// A decoded packet arrived, or the user (or a wake/unlock) asked for a
    /// connection: retry promptly again.
    public mutating func reset() {
        failures = 0
    }

    /// Record how a connection ended and return the delay before the next try.
    public mutating func nextDelay(after outcome: Outcome) -> TimeInterval {
        switch outcome {
        case .workingLinkDropped:
            failures = 0
            return Self.baseDelay
        case .neverUsable:
            let delay = Self.delay(afterFailures: failures)
            failures += 1
            return delay
        }
    }

    /// Delay before retrying when nothing was found, without counting a failure:
    /// the idle rescan pause, or the current backoff if that is longer.
    public var idleDelay: TimeInterval {
        failures == 0 ? Self.idleRescanDelay : max(Self.idleRescanDelay, Self.delay(afterFailures: failures))
    }

    public static func outcome(receivedPacket: Bool, pairingFailure: Bool) -> Outcome {
        (receivedPacket && !pairingFailure) ? .workingLinkDropped : .neverUsable
    }

    // Error domains and codes from CoreBluetooth, spelled out so this file does
    // not need the framework.
    public static let cbErrorDomain = "CBErrorDomain"
    public static let cbATTErrorDomain = "CBATTErrorDomain"

    /// Whether an error means the link cannot be encrypted or authenticated,
    /// i.e. the diffuser and this Mac no longer agree on a pairing.
    public static func isPairingFailure(domain: String, code: Int) -> Bool {
        switch domain {
        case cbErrorDomain:
            // peerRemovedPairingInformation (14), encryptionTimedOut (15)
            return code == 14 || code == 15
        case cbATTErrorDomain:
            // insufficientAuthentication (5), insufficientAuthorization (8),
            // insufficientEncryptionKeySize (12), insufficientEncryption (15)
            return [5, 8, 12, 15].contains(code)
        default:
            return false
        }
    }

    public static func isPairingFailure(_ error: Error?) -> Bool {
        guard let error = error as NSError? else { return false }
        return isPairingFailure(domain: error.domain, code: error.code)
    }
}
