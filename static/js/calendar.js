document.addEventListener('DOMContentLoaded', function () {
    // Guild detail calendar (weekly view)
    var guildCalEl = document.getElementById('guild-calendar');
    if (guildCalEl) {
        var guildSlug = guildCalEl.dataset.guild;
        var calendar = new FullCalendar.Calendar(guildCalEl, {
            initialView: 'timeGridWeek',
            headerToolbar: {
                left: 'prev,next today',
                center: 'title',
                right: 'timeGridWeek,listWeek',
            },
            events: function (info, successCallback, failureCallback) {
                fetch(
                    '/api/calendar-events/?guild=' +
                        guildSlug +
                        '&start=' +
                        info.startStr +
                        '&end=' +
                        info.endStr
                )
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        successCallback(data);
                    })
                    .catch(function (error) {
                        failureCallback(error);
                    });
            },
            eventClick: function (info) {
                alert(info.event.title);
            },
            height: 'auto',
            nowIndicator: true,
        });
        calendar.render();
    }

    // Dashboard calendar (monthly view) — wired in Task 8
    var dashCalEl = document.getElementById('dashboard-calendar');
    if (dashCalEl) {
        var dashCalendar = new FullCalendar.Calendar(dashCalEl, {
            initialView: 'dayGridMonth',
            headerToolbar: {
                left: 'prev,next today',
                center: 'title',
                right: 'dayGridMonth,timeGridWeek,listMonth',
            },
            events: function (info, successCallback, failureCallback) {
                fetch(
                    '/api/calendar-events/?start=' + info.startStr + '&end=' + info.endStr
                )
                    .then(function (response) {
                        return response.json();
                    })
                    .then(function (data) {
                        successCallback(data);
                    })
                    .catch(function (error) {
                        failureCallback(error);
                    });
            },
            height: 'auto',
        });
        dashCalendar.render();
    }
});
