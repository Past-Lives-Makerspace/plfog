/**
 * Session Calendar — Alpine.js component for visual class session scheduling.
 * Renders a month-view grid; click days to add sessions, click dots to edit.
 * Generates hidden inputs matching Django's inline formset naming convention.
 *
 * Registered with Alpine via Alpine.data() so the component is available in
 * Alpine's registry before HTMX-swapped fragments are initialized. Defining a
 * bare global and loading it inside the fragment races htmx:afterSettle's
 * Alpine.initTree, which evaluates x-data before the deferred script executes.
 */
(function () {
    "use strict";

    const sessionCalendar = (initialSessions, initialForms) => ({
        viewYear: new Date().getFullYear(),
        viewMonth: new Date().getMonth(),

        sessions: [],
        _nextKey: 0,
        initialForms: initialForms || 0,

        popoverOpen: false,
        popoverMode: 'add',
        popoverDate: '',
        editingKey: null,
        popoverTime: '10:00',
        popoverDuration: 2,

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
            if (this.sessions.length > 0) {
                const first = new Date(this.sessions[0].starts_at);
                this.viewYear = first.getFullYear();
                this.viewMonth = first.getMonth();
            }
        },

        // --- Calendar navigation ---
        get monthLabel() {
            return new Date(this.viewYear, this.viewMonth).toLocaleDateString('en-US', { month: 'long', year: 'numeric' });
        },
        prevMonth() {
            if (this.viewMonth === 0) { this.viewYear--; this.viewMonth = 11; }
            else { this.viewMonth--; }
        },
        nextMonth() {
            if (this.viewMonth === 11) { this.viewYear++; this.viewMonth = 0; }
            else { this.viewMonth++; }
        },
        goToday() {
            const now = new Date();
            this.viewYear = now.getFullYear();
            this.viewMonth = now.getMonth();
        },

        // --- Calendar grid ---
        get calendarDays() {
            const first = new Date(this.viewYear, this.viewMonth, 1);
            const startDay = first.getDay();
            const days = [];
            const today = new Date();
            const todayStr = this._dateStr(today);

            for (let i = 0; i < 42; i++) {
                const d = new Date(this.viewYear, this.viewMonth, 1 - startDay + i);
                const dateStr = this._dateStr(d);
                days.push({
                    date: d,
                    dateStr: dateStr,
                    inMonth: d.getMonth() === this.viewMonth,
                    isToday: dateStr === todayStr,
                });
            }
            return days;
        },

        // --- Session queries ---
        sessionsOnDate(dateStr) {
            return this.sessions.filter(s => !s.DELETE && s.starts_at.startsWith(dateStr));
        },
        dateHasSessions(dateStr) {
            return this.sessions.some(s => !s.DELETE && s.starts_at.startsWith(dateStr));
        },

        // --- Popover ---
        openAddPopover(dateStr) {
            this.popoverMode = 'add';
            this.popoverDate = dateStr;
            this.popoverTime = '10:00';
            this.popoverDuration = 2;
            this.editingKey = null;
            this.popoverOpen = true;
        },
        openEditPopover(key) {
            const s = this.sessions.find(x => x._key === key);
            if (!s) return;
            this.popoverMode = 'edit';
            this.editingKey = key;
            this.popoverDate = s.starts_at.slice(0, 10);
            this.popoverTime = s.starts_at.slice(11, 16);
            const startMs = new Date(s.starts_at).getTime();
            const endMs = new Date(s.ends_at).getTime();
            this.popoverDuration = Math.round((endMs - startMs) / 3600000 * 2) / 2 || 2;
            this.popoverOpen = true;
        },
        closePopover() { this.popoverOpen = false; },

        savePopover() {
            if (!this.popoverDate || !this.popoverTime) return;
            const starts_at = this.popoverDate + 'T' + this.popoverTime;
            const endDate = new Date(new Date(starts_at).getTime() + this.popoverDuration * 3600000);
            const ends_at = this._dateTimeStr(endDate);

            if (this.popoverMode === 'edit' && this.editingKey !== null) {
                const s = this.sessions.find(x => x._key === this.editingKey);
                if (s) { s.starts_at = starts_at; s.ends_at = ends_at; }
            } else {
                this.sessions.push({
                    _key: this._nextKey++,
                    id: '',
                    starts_at: starts_at,
                    ends_at: ends_at,
                    DELETE: false,
                });
            }
            this.closePopover();
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
