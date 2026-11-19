"""Behavioural tests for the Swift app's pure protocol layer.

Protocol.swift is a hand-maintained port of airspray/protocol.py. The parity test
compares constants by parsing the source. This one compiles the real Swift and
exercises it, so decoding and validation logic is covered too.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SWIFT_SOURCES = [
    ROOT / "macos" / "AirSpray" / "Sources" / "Protocol.swift",
    ROOT / "macos" / "AirSpray" / "Sources" / "RefillModel.swift",
    ROOT / "macos" / "AirSpray" / "Sources" / "Config.swift",
    ROOT / "macos" / "AirSpray" / "Sources" / "Schema.swift",
    ROOT / "macos" / "AirSpray" / "Sources" / "ReconnectPolicy.swift",
    ROOT / "macos" / "AirSpray" / "Sources" / "ScheduleLoadTracker.swift",
]
HARNESS = ROOT / "tests" / "swift" / "main.swift"

pytestmark = pytest.mark.skipif(
    shutil.which("swiftc") is None, reason="swiftc not available"
)


@pytest.fixture(scope="module")
def harness_results(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    binary: Path = tmp_path_factory.mktemp("swift") / "harness"
    build = subprocess.run(
        [
            "swiftc",
            "-O",
            *[str(p) for p in SWIFT_SOURCES],
            str(HARNESS),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
    )
    assert build.returncode == 0, f"swiftc failed:\n{build.stderr}"

    run = subprocess.run([str(binary)], capture_output=True, text=True)
    results: dict[str, str] = {}
    for line in run.stdout.splitlines():
        if "\t" in line:
            name, outcome = line.split("\t", 1)
            results[name] = outcome
    assert results, f"harness produced no results:\n{run.stdout}\n{run.stderr}"
    return results


EXPECTED_CHECKS = [
    "decode_valid_cmd",
    "decode_valid_payload",
    "decode_rejects_bad_checksum",
    "decode_scans_past_leading_garbage",
    "decode_rejects_truncated",
    "decode_rejects_short_payload",
    "name_accepts_air_wick_mist",
    "name_accepts_airwick_reeses",
    "name_rejects_other_mist_device",
    "name_rejects_substring_match",
    "name_rejects_empty",
    "battery_rejects_spurious_zero",
    "battery_accepts_zero_when_low",
    "battery_accepts_normal_level",
    "battery_rejects_out_of_range",
    "room_table_matches_firmware",
    "schedule_packs_name_and_slot",
    "intensity_zero_preserved",
    "refill_full_level_starts_new_bottle",
    "refill_partial_level_keeps_bottle",
    "refill_partial_level_marked_new_starts_bottle",
    "refill_summary_leads_with_percentage",
    "refill_summary_includes_forecast",
    "refill_summary_clamps_above_full",
    "refill_summary_clamps_below_empty",
    "battery_label_full",
    "battery_label_good",
    "battery_label_medium",
    "battery_label_low",
    "battery_label_critical",
    "battery_label_clamps_out_of_range",
    "config_round_trips_room_id",
    "config_round_trips_refill",
    "legacy_config_keeps_room",
    "legacy_config_keeps_refill",
    "legacy_config_keeps_address",
    "legacy_config_defaults_missing_key",
    "config_round_trips_fragrance_id",
    "fragrance_catalogue_covers_full_range",
    "fragrance_catalogue_has_no_blank_names",
    "fragrance_name_lookup",
    "fragrance_catalogue_includes_newest",
    "fragrance_name_with_ampersand",
    "refill_clamp_above_full",
    "refill_clamp_below_empty",
    "refill_clamp_passes_valid_through",
    "fragrance_order_has_every_entry",
    "fragrance_order_preserves_ids",
    "fragrance_order_is_alphabetical",
    "schedule_decode_slot",
    "schedule_decode_name",
    "schedule_decode_enabled",
    "schedule_decode_window",
    "schedule_decode_weekdays",
    "schedule_decode_repeats",
    "schedule_decode_intensity",
    "schedule_decode_rejects_empty_slot",
    "schedule_decode_rejects_short_payload",
    "schedule_decode_disabled_slot",
    "schedule_round_trips",
    "days_every_day",
    "days_weekday_run",
    "days_weekend_pair",
    "days_scattered",
    "days_single",
    "days_none",
    "slot_configured_when_enabled",
    "slot_free_when_cleared",
    "slot_configured_when_disabled_but_set",
    "slot_free_when_window_all_zero_even_if_enabled",
    "normalize_expands_all_zero_window",
    "normalize_keeps_start_at_midnight",
    "normalize_leaves_real_window_alone",
    "payload_never_writes_all_zero_window",
    "normalized_payload_reads_back_as_occupied",
    "consumption_zero_while_paused",
    "consumption_zero_for_unknown_level",
    "consumption_medium_matches_python",
    "consumption_rises_with_intensity",
    "installed_date_is_year_month_day",
    "room_fragrance_clamps_high",
    "room_fragrance_clamps_low",
    "schedule_payload_clamps_out_of_range",
    "schema_json_is_snake_case",
    "schema_json_has_no_camel_case_keys",
    "schema_json_omits_invented_battery_percentage",
    "shared_config_reads_cli_refill",
    "shared_config_keeps_cli_only_keys",
    "shared_config_writes_own_keys",
    "config_update_writes_timestamp",
    "config_nil_clears_value",
    "config_nil_removes_key_from_file",
    "config_nil_clear_keeps_other_values",
    "config_null_reads_as_absent",
    "config_wrong_type_falls_back_per_key",
    "config_update_does_not_revert_refill",
    "config_update_does_not_revert_puffs",
    "config_update_applies_own_change",
    "config_update_without_change_does_not_write",
    "config_update_keeps_cli_only_keys",
    "config_write_leaves_no_temp_files",
    "config_corrupt_file_loads_fresh",
    "config_corrupt_file_is_moved_aside",
    "config_corrupt_name_is_suffixed",
    "config_corrupt_copy_survives_next_save",
    "config_empty_file_is_moved_aside_not_overwritten",
    "legacy_store_migrates_when_config_missing",
    "load_does_not_create_the_file",
    "legacy_store_ignored_once_config_exists",
    "legacy_store_does_not_overwrite_install_date",
    "legacy_store_ignored_for_corrupt_config",
    "reconnect_working_link_always_retries_at_base",
    "reconnect_unusable_link_backs_off",
    "reconnect_backoff_caps_at_five_minutes",
    "reconnect_working_link_clears_backoff",
    "reconnect_reset_restores_base_delay",
    "reconnect_outcome_packet_means_working",
    "reconnect_outcome_no_packet_means_unusable",
    "reconnect_outcome_pairing_error_means_unusable",
    "reconnect_idle_delay_default",
    "reconnect_idle_delay_respects_backoff",
    "pairing_error_peer_removed_pairing_info",
    "pairing_error_encryption_timed_out",
    "pairing_error_att_insufficient_authentication",
    "pairing_error_att_insufficient_encryption",
    "pairing_error_ignores_plain_timeout",
    "pairing_error_ignores_nil",
    "pairing_error_reads_nserror",
    "schedule_edit_without_changes_is_identity",
    "schedule_edit_keeps_name_id",
    "schedule_edit_keeps_repeat_flag",
    "schedule_edit_keeps_slot",
    "schedule_edit_keeps_paused_intensity",
    "schedule_edit_applies_exposed_fields",
    "schedule_edit_round_trips_through_wire",
    "schedule_editor_offers_paused_only_when_stored",
    "schedule_load_waits_for_every_slot",
    "schedule_load_completes_when_all_answer",
    "schedule_load_attributes_unaddressed_answer",
    "schedule_load_completes_with_slot_zero_answers",
    "schedule_load_ignores_stray_unaddressed_answer",
    "schedule_load_reports_silent_slots_pending",
    "schedule_load_completes_with_silent_slots",
    "schedule_load_real_answer_overrides_inference",
    "diagnostics_schedules_skip_free_slots_and_sort",
    "diagnostics_schedule_times_are_zero_padded",
    "diagnostics_schedule_days_and_repeat",
    "diagnostics_default_install_date_is_not_invented",
    "schedule_info_splits_status_and_slot",
    "schedule_info_zero_is_inactive",
    "room_id_validation",
    "fragrance_id_validation",
]


@pytest.mark.parametrize("check_name", EXPECTED_CHECKS)
def test_swift_protocol_check(harness_results: dict[str, str], check_name: str) -> None:
    assert check_name in harness_results, "check did not run"
    assert harness_results[check_name] == "OK", harness_results[check_name]
