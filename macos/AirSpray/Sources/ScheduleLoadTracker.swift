import Foundation

/// Bookkeeping for one read of the diffuser's schedule slots.
///
/// A slot is done when the diffuser answered for it, or when it has been
/// identified as empty. How the firmware answers a query for an empty slot is
/// not pinned down, so all three plausible behaviours finish the load:
///   1. it answers with the slot id and an all-zero window (`didAnswer`).
///   2. it answers with slot id 0, which names no slot (`didAnswerWithoutSlot`
///      attributes it to the oldest unanswered query).
///   3. it does not answer at all (`markSilentSlotsEmpty`, used once the caller
///      has re-queried and knows the link is still delivering packets).
public struct ScheduleLoadTracker: Equatable {
    /// Slots that have neither answered nor been identified as empty.
    public private(set) var pending: Set<Int>
    /// Queried slots still waiting for an answer, oldest first.
    public private(set) var outstanding: [Int] = []
    /// Slots concluded to be empty without a slot-addressed answer.
    public private(set) var inferredEmpty: Set<Int> = []

    public init(slots: ClosedRange<Int>) {
        pending = Set(slots)
    }

    public var isComplete: Bool { pending.isEmpty }

    public mutating func didQuery(slot: Int) {
        guard pending.contains(slot), !outstanding.contains(slot) else { return }
        outstanding.append(slot)
    }

    public mutating func didAnswer(slot: Int) {
        pending.remove(slot)
        outstanding.removeAll { $0 == slot }
        inferredEmpty.remove(slot)
    }

    /// An answer carrying slot id 0. Returns the slot it was attributed to.
    @discardableResult
    public mutating func didAnswerWithoutSlot() -> Int? {
        guard !outstanding.isEmpty else { return nil }
        let slot = outstanding.removeFirst()
        pending.remove(slot)
        inferredEmpty.insert(slot)
        return slot
    }

    /// Give up waiting: every slot still pending is treated as empty.
    public mutating func markSilentSlotsEmpty() {
        inferredEmpty.formUnion(pending)
        pending.removeAll()
        outstanding.removeAll()
    }
}
