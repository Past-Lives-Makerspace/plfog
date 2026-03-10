/* Hub JS - Theme toggle & profile dropdown */

(function () {
    'use strict';

    // --- Profile Dropdown ---
    var toggle = document.getElementById('profile-toggle');
    var dropdown = document.getElementById('profile-dropdown');
    var avatar = toggle ? toggle.querySelector('.hub-topbar__avatar') : null;

    if (avatar && dropdown) {
        avatar.addEventListener('click', function (e) {
            e.stopPropagation();
            var isOpen = dropdown.classList.toggle('open');
            avatar.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
        });

        document.addEventListener('click', function (e) {
            if (!toggle.contains(e.target)) {
                dropdown.classList.remove('open');
                avatar.setAttribute('aria-expanded', 'false');
            }
        });

        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && dropdown.classList.contains('open')) {
                dropdown.classList.remove('open');
                avatar.setAttribute('aria-expanded', 'false');
                avatar.focus();
            }
        });
    }

    // --- Theme Toggle ---
    var themeBtn = document.getElementById('theme-toggle');
    var themeLabel = document.getElementById('theme-label');

    function updateLabel() {
        var isLight = document.documentElement.getAttribute('data-theme') === 'light';
        if (themeLabel) {
            themeLabel.textContent = isLight ? 'Dark Mode' : 'Light Mode';
        }
    }

    if (themeBtn) {
        updateLabel();
        themeBtn.addEventListener('click', function () {
            var isLight = document.documentElement.getAttribute('data-theme') === 'light';
            if (isLight) {
                document.documentElement.removeAttribute('data-theme');
                localStorage.removeItem('theme');
            } else {
                document.documentElement.setAttribute('data-theme', 'light');
                localStorage.setItem('theme', 'light');
            }
            updateLabel();
        });
    }
})();
