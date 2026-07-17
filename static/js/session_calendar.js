/**
 * Session Scheduler — Alpine.js component for adding class sessions.
 * A compact inline "add a session" row (date + start time + duration) appends
 * to a visible list. Generates hidden inputs matching Django's inline formset
 * naming convention.
 *
 * Registered with Alpine via Alpine.data() so the component is available in
 * Alpine's registry before HTMX-swapped fragments are initialized. Defining a
 * bare global and loading it inside the fragment races htmx:afterSettle's
 * Alpine.initTree, which evaluates x-data before the deferred script executes.
 */
(function () {
    "use strict";

    // Half-hour start times, 6:00 AM–9:30 PM, as {value:"HH:MM", label:"6:00 AM"} —
    // the same half-hour grid as the hub's time dropdowns (Rule 19: no per-minute pickers).
    const buildHalfHourTimes = () => {
        const opts = [];
        for (let hour = 6; hour < 22; hour++) {
            for (const minute of [0, 30]) {
                const value = String(hour).padStart(2, "0") + ":" + String(minute).padStart(2, "0");
                const hour12 = hour % 12 || 12;
                const suffix = hour < 12 ? "AM" : "PM";
                const label = hour12 + ":" + String(minute).padStart(2, "0") + " " + suffix;
                opts.push({ value, label });
            }
        }
        return opts;
    };

    const sessionCalendar = (initialSessions, initialForms) => ({
        sessions: [],
        _nextKey: 0,
        initialForms: initialForms || 0,

        // Inline "add a session" fields
        newDate: '',
        newTime: '10:00',
        newDuration: 2,

        timeOptions: buildHalfHourTimes(),

        durationOptions: [
            { value: 1, label: '1 hour' },
            { value: 1.5, label: '1.5 hours' },
            { value: 2, label: '2 hours' },
            { value: 2.5, label: '2.5 hours' },
            { value: 3, label: '3 hours' },
            { value: 4, label: '4 hours' },
            { value: 6, label: '6 hours' },
            { value: 8, label: '8 hours' },
        ],

        init() {
            (initialSessions || []).forEach(s => {
                this.sessions.push({
                    _key: this._nextKey++,
                    id: String(s.id || ''),
                    starts_at: s.starts_at,
                    ends_at: s.ends_at,
                    DELETE: !!s.DELETE,
                });
            });
        },

        // Read the inline fields, compute ends_at = start + duration, append a
        // new session, then reset the inputs for the next entry.
        addSession() {
            if (!this.newDate || !this.newTime) return;
            const starts_at = this.newDate + 'T' + this.newTime;
            const endDate = new Date(new Date(starts_at).getTime() + this.newDuration * 3600000);
            const ends_at = this._dateTimeStr(endDate);
            this.sessions.push({
                _key: this._nextKey++,
                id: '',
                starts_at: starts_at,
                ends_at: ends_at,
                DELETE: false,
            });
            this.newDate = '';
            this.newTime = '10:00';
            this.newDuration = 2;
        },

        deleteSession(key) {
            const idx = this.sessions.findIndex(x => x._key === key);
            if (idx === -1) return;
            if (this.sessions[idx].id) {
                this.sessions[idx].DELETE = true;
            } else {
                this.sessions.splice(idx, 1);
            }
        },

        // --- Formset interface ---
        get activeSessions() {
            return this.sessions.filter(s => !s.DELETE).sort((a, b) => a.starts_at.localeCompare(b.starts_at));
        },
        get allSessionsForForm() {
            const existing = this.sessions.filter(s => s.id);
            const added = this.sessions.filter(s => !s.id);
            return [...existing, ...added];
        },
        get totalForms() { return this.allSessionsForForm.length; },

        // --- Helpers ---
        _pad(n) { return n < 10 ? '0' + n : '' + n; },
        _dateStr(d) {
            return d.getFullYear() + '-' + this._pad(d.getMonth() + 1) + '-' + this._pad(d.getDate());
        },
        _dateTimeStr(d) {
            return this._dateStr(d) + 'T' + this._pad(d.getHours()) + ':' + this._pad(d.getMinutes());
        },
    });

    const registerComponent = () => {
        if (window.Alpine && window.Alpine.components && window.Alpine.components['sessionCalendar']) return;
        Alpine.data('sessionCalendar', sessionCalendar);
    };

    if (window.Alpine) {
        registerComponent();
    } else {
        document.addEventListener('alpine:init', registerComponent);
    }
})();
