# Shared configuration

The command-line tool and the macOS menu bar app keep their settings in the same
file, so both show the same refill level, room, fragrance and paired diffuser:

```txt
~/.airspray_config.json
```

Both apps can be running at once, and both save to this file. They follow the
same rules so that neither one undoes the other's changes.

## The rules

- **Read the file again before every save, and change only what you meant to.**
  Neither app writes back a copy of the settings it loaded earlier. If it did,
  anything the other app had changed in the meantime would be silently reverted.
- **Subtract refill usage from the level that is in the file now.** If you put
  in a new bottle and reset it to 100% in one app, the other app subtracts its
  next bit of usage from 100%, not from the old level it remembered.
- **Save by writing a new file and swapping it in.** The settings are written to
  a temporary file that then replaces the real one in a single step, so the
  other app can never read a half-written file.
- **Never treat an unreadable file as an empty one.** If the file exists but
  can't be read as JSON, it is moved aside to `~/.airspray_config.json.corrupt`
  and the app starts with fresh settings. Without this, one bad read would
  replace all your settings with defaults.
- **To clear a setting, remove it from the file.** Some settings can be "not
  set", such as the time the diffuser was last running. Clearing one deletes its
  entry. Leaving the old value behind is what once made a long pause count as
  hours of spraying.

## Limits

- There is no lock between the two apps. If both save within the same few
  milliseconds, one of the two changes can still be lost.
- Both apps measure refill usage from the same "last running" time in the file,
  so running them together does not count usage twice.
- Both apps will react when you arrive or leave. Use only one of them for
  presence automation.

## Settings from older versions

An older version of the command-line tool kept the refill level in
`~/.airspray/state.json`. That file is imported only when the shared file has
no refill level yet. The command-line tool then renames it to
`state.json.migrated` so it is not read again.

The keys are the same for both apps (`lastAddress`, `refillPercentage`,
`lastRoomId`, and so on). Each app leaves alone any key it doesn't use.
