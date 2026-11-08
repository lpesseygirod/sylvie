"""AppleScripts for Sylvie's actions. Every value comes in through `argv` (see actions/osa.py)."""

ISO_TO_DATE = """
on isoToDate(s)
    -- "YYYY-MM-DDTHH:MM" → AppleScript date (set day to 1 first so month changes can't overflow)
    set d to current date
    set day of d to 1
    set year of d to (text 1 thru 4 of s) as integer
    set month of d to (text 6 thru 7 of s) as integer
    set day of d to (text 9 thru 10 of s) as integer
    set hours of d to (text 12 thru 13 of s) as integer
    set minutes of d to (text 15 thru 16 of s) as integer
    set seconds of d to 0
    return d
end isoToDate
"""

# Volume commands change the Mac's own volume, not Spotify's: the app turns Spotify down to 12 while
# Sylvie listens and puts it back afterwards, which undid her changes (2026-11-08: "put the volume to
# the max" set Spotify to 100, where it already was; the Mac stayed at 81). The result says the new level.
SPOTIFY_COMMAND = """
on run argv
    set cmd to item 1 of argv
    if cmd is in {"volume_up", "volume_down", "set_volume"} then
        set v to output volume of (get volume settings)
        if cmd is "volume_up" then
            set v to v + 12
        else if cmd is "volume_down" then
            set v to v - 12
        else
            set v to (item 2 of argv) as integer
        end if
        if v > 100 then set v to 100
        if v < 0 then set v to 0
        set volume output volume v without output muted
        set volText to "Mac volume now " & (output volume of (get volume settings)) & "%. "
    else
        set volText to ""
    end if
    tell application "Spotify"
        if cmd is "play" then
            play
        else if cmd is "pause" then
            pause
        else if cmd is "next" then
            next track
        else if cmd is "previous" then
            previous track
        end if
        if player state is playing then
            return volText & (name of current track) & " — " & (artist of current track)
        end if
        return volText & "paused"
    end tell
end run
"""

SPOTIFY_PLAY_URI = """
on run argv
    tell application "Spotify"
        play track (item 1 of argv)
        delay 0.5
        return (name of current track) & " — " & (artist of current track)
    end tell
end run
"""

OPEN_LOCATION = """
on run argv
    open location (item 1 of argv)
end run
"""

CALENDAR_EVENTS = """
on run argv
    set startD to (current date) + ((item 1 of argv) as integer) * days
    set time of startD to 0
    set n to 1
    if (count of argv) > 1 then set n to (item 2 of argv) as integer
    set endD to startD + n * days
    set out to ""
    tell application "Calendar"
        repeat with c in calendars
            set evs to (every event of c whose start date ≥ startD and start date < endD)
            repeat with e in evs
                set out to out & ((start date of e) as «class isot» as string) & tab & (summary of e) & tab & (name of c) & linefeed
            end repeat
        end repeat
    end tell
    return out
end run
"""

CALENDAR_CREATE = (
    ISO_TO_DATE
    + """
on run argv
    set t to item 1 of argv
    set startD to my isoToDate(item 2 of argv)
    set mins to (item 3 of argv) as integer
    set calName to item 4 of argv
    tell application "Calendar"
        if calName is "" then
            set cal to first calendar whose writable is true
        else
            set cal to calendar calName
        end if
        set e to make new event at end of events of cal with properties {summary:t, start date:startD, end date:startD + mins * minutes}
        return uid of e
    end tell
end run
"""
)

CALENDAR_DELETE = """
on run argv
    set u to item 1 of argv
    tell application "Calendar"
        repeat with c in calendars
            set hits to (every event of c whose uid is u)
            repeat with e in hits
                delete e
            end repeat
        end repeat
    end tell
end run
"""

REMINDER_CREATE = (
    ISO_TO_DATE
    + """
on run argv
    set t to item 1 of argv
    set due to item 2 of argv
    tell application "Reminders"
        set r to make new reminder with properties {name:t}
        if due is not "" then set due date of r to my isoToDate(due)
        return id of r
    end tell
end run
"""
)

REMINDER_DELETE = """
on run argv
    tell application "Reminders" to delete (reminder id (item 1 of argv))
end run
"""

MAIL_READ = """
on run argv
    set n to (item 1 of argv) as integer
    set onlyUnread to (item 2 of argv) is "1"
    set out to ""
    set k to 0
    tell application "Mail"
        set total to count of messages of inbox
        if total < n * 4 then
            set lim to total
        else
            set lim to n * 4
        end if
        repeat with i from 1 to lim
            set m to message i of inbox
            if (not onlyUnread) or (read status of m is false) then
                set c to content of m
                if length of c > 1200 then set c to text 1 thru 1200 of c
                set out to out & "From: " & (sender of m) & linefeed & "Subject: " & (subject of m) & linefeed & "Date: " & ((date received of m) as string) & linefeed & c & linefeed & "-----" & linefeed
                set k to k + 1
                if k ≥ n then exit repeat
            end if
        end repeat
    end tell
    return out
end run
"""

MAIL_COMPOSE = """
on run argv
    set toAddr to item 1 of argv
    set subj to item 2 of argv
    set body to item 3 of argv
    set fromAddr to item 4 of argv
    set doSend to (item 5 of argv) is "1"
    tell application "Mail"
        set m to make new outgoing message with properties {subject:subj, content:body, visible:(not doSend)}
        if fromAddr is not "" then set sender of m to fromAddr
        tell m to make new to recipient at end of to recipients with properties {address:toAddr}
        if doSend then
            send m
            return "sent"
        end if
        return "draft"
    end tell
end run
"""

NOTIFY = """
on run argv
    display notification (item 2 of argv) with title (item 1 of argv) sound name "Glass"
end run
"""

# Every contact: "name<TAB>phone,phone<TAB>email,email" per line (Lea's Contacts, read on the Mac).
CONTACTS_ALL = """
on run argv
    set out to ""
    tell application "Contacts"
        repeat with p in people
            set ph to ""
            repeat with x in phones of p
                set ph to ph & (value of x) & ","
            end repeat
            set em to ""
            repeat with x in emails of p
                set em to em & (value of x) & ","
            end repeat
            set out to out & (name of p) & tab & ph & tab & em & linefeed
        end repeat
    end tell
    return out
end run
"""

# iMessage (or SMS through her iPhone) with the Messages app.
MESSAGES_SEND = """
on run argv
    set theText to item 1 of argv
    set theHandle to item 2 of argv
    tell application "Messages"
        set svc to 1st account whose service type = iMessage
        send theText to participant theHandle of svc
    end tell
    return "sent"
end run
"""

NOTE_CREATE = """
on run argv
    tell application "Notes"
        set n to make new note with properties {name:(item 1 of argv), body:(item 2 of argv)}
        return id of n
    end tell
end run
"""

NOTE_DELETE = """
on run argv
    tell application "Notes" to delete (note id (item 1 of argv))
end run
"""

# Lea's open reminders: "due<TAB>title<TAB>list" per line (due empty when there's none).
REMINDERS_LIST = """
on run argv
    set out to ""
    tell application "Reminders"
        repeat with l in lists
            repeat with r in (reminders of l whose completed is false)
                set d to ""
                try
                    set d to (due date of r) as «class isot» as string
                end try
                set out to out & d & tab & (name of r) & tab & (name of l) & linefeed
            end repeat
        end repeat
    end tell
    return out
end run
"""
