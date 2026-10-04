"""Turn bare web addresses in sanitized HTML into links, and harden every link, in linear time.

The sanitizers (:mod:`core.html_sanitize`, :mod:`membership.markdown`) clean HTML with ``nh3``,
which has no auto-linker. :func:`linkify` is the second half of the pipeline: it runs over
``nh3``'s output and

* passes every ``<a>`` start tag, the author's own links included, through a hardener that sets
  ``rel`` and ``target``;
* turns an address typed as plain text (``https://pastlives.space/classes``,
  ``www.example.com``, ``example.org/path``) into a link carrying the same hardening;
* leaves the text inside an existing ``<a>`` alone.

The matching rules are the ones ``bleach.linkify`` used, so text links in the same places it
always did: a run of dotted labels ending in a known top-level domain, an optional port and
path, an optional scheme with optional user info, surrounding brackets and trailing ``.`` or
``,`` left outside the link, and ``http://`` added when no scheme was typed. Four things
differ on purpose:

* an address typed with the ``data:`` scheme stays plain text, since a page should never offer
  a link to one;
* a match that starts with a hyphen stays plain text. bleach linked the tail of an e-mail
  address with a hyphenated domain ("info@past-lives.org" became "info@past" and a link to
  "http://-lives.org"), and no host name starts with a hyphen;
* ``&nbsp;`` ends an address, as a typed space does;
* the scan is linear in the length of the text.

The input must be ``nh3`` output: every ``<`` in text is escaped, every attribute value is
double quoted with ``>`` and ``"`` escaped, and tag names are lower case. That is what makes a
split on ``<[^>]*>`` exact.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Callable

#: A link hardener: takes an anchor's attributes (serialized values, in source order) and
#: returns the attributes the anchor should carry.
LinkHardener = Callable[[dict[str, str]], dict[str, str]]

# bleach's top-level domain list (bleach.linkifier.TLDS). An address must end in one of these.
_TLDS = frozenset(
    """ac ad ae aero af ag ai al am an ao aq ar arpa as asia at au aw ax az
    ba bb bd be bf bg bh bi biz bj bm bn bo br bs bt bv bw by bz ca cat
    cc cd cf cg ch ci ck cl cm cn co com coop cr cu cv cx cy cz de dj dk
    dm do dz ec edu ee eg er es et eu fi fj fk fm fo fr ga gb gd ge gf gg
    gh gi gl gm gn gov gp gq gr gs gt gu gw gy hk hm hn hr ht hu id ie il
    im in info int io iq ir is it je jm jo jobs jp ke kg kh ki km kn kp
    kr kw ky kz la lb lc li lk lr ls lt lu lv ly ma mc md me mg mh mil mk
    ml mm mn mo mobi mp mq mr ms mt mu museum mv mw mx my mz na name nc ne
    net nf ng ni nl no np nr nu nz om org pa pe pf pg ph pk pl pm pn post
    pr pro ps pt pw py qa re ro rs ru rw sa sb sc sd se sg sh si sj sk sl
    sm sn so sr ss st su sv sx sy sz tc td tel tf tg th tj tk tl tm tn to
    tp tr travel tt tv tw tz ua ug uk us uy uz va vc ve vg vi vn vu wf ws
    xn xxx ye yt yu za zm zw""".split()
)
# The schemes bleach recognised at the start of an address. Recognising all of them keeps the
# edges of a match where bleach put them; one of _UNLINKED_SCHEMES is left as text.
_RECOGNISED_SCHEMES = frozenset(
    """afs aim callto data ed2k feed ftp gopher http https irc mailto news nntp rsync rtsp sftp
    ssh tag telnet urn webcal xmpp""".split()
)
_UNLINKED_SCHEMES = frozenset({"data"})

_TAG_RE = re.compile(r"(<[^>]*>)")
_ATTR_RE = re.compile(r'([^\s="]+)="([^"]*)"')
# A maximal run of the characters a host name is made of.
_HOST_RUN_RE = re.compile(r"[\w.-]+")
# Where an address may begin: a word that does not follow a word character, "@" or "."; or a
# hyphen right after a word character (bleach's \b then [\w-]+).
_START_RE = re.compile(r"(?<![\w@.])\w|(?<=\w)-")
_WORD_RE = re.compile(r"\w+")
_DIGITS_RE = re.compile(r"[0-9]+")
# A path runs to a space, a character no URL holds, or an "&nbsp;" (a typed space, escaped). The
# "&nbsp;" stop is part of the pattern: matching to the end of the run and cutting back would
# rescan the same tail from every address in it.
_PATH_RE = re.compile(r"""[^\s{}|\\^`<>"&]*(?:&(?!nbsp;)[^\s{}|\\^`<>"&]*)*""")
_SCHEME_PREFIX_RE = re.compile(r"([\w-]+):")


def _is_word(char: str) -> bool:
    """True for a regex ``\\w`` character."""
    return char.isalnum() or char == "_"


class _Hosts:
    """The dotted host runs of one text, split into labels, with each label's best ending.

    Built once per text so that every candidate start is answered in constant time, which keeps
    the whole scan linear.
    """

    def __init__(self, text: str) -> None:
        self.text = text
        self.starts: list[int] = []
        self.ends: list[int] = []
        self.dotted: list[bool] = []
        self.best: list[int] = []
        for run in _HOST_RUN_RE.finditer(text):
            self._add_run(run.start(), run.end())

    def _add_run(self, run_start: int, run_end: int) -> None:
        first = len(self.starts)
        cursor = run_start
        for label in self.text[run_start:run_end].split("."):
            self.starts.append(cursor)
            self.ends.append(cursor + len(label))
            self.dotted.append(cursor + len(label) < run_end)
            self.best.append(-1)
            cursor += len(label) + 1
        # Right to left: a label that is non-empty and followed by a dot defers to the furthest
        # top-level domain reachable after it (the pattern's labels are greedy); otherwise the
        # label can only end the address itself.
        for index in range(len(self.starts) - 1, first - 1, -1):
            further = self.best[index + 1] if self.dotted[index] else -1
            if further >= 0 and self.ends[index] > self.starts[index]:
                self.best[index] = further
            else:
                self.best[index] = self._tld_end(index)

    def _tld_end(self, index: int) -> int:
        """Where an address ends if label ``index`` is its top-level domain, or -1."""
        text, start, end = self.text, self.starts[index], self.ends[index]
        label = text[start:end]
        dash = label.find("-")
        if dash >= 0:
            # A hyphen ends the top-level domain: "example.co-op" links "example.co".
            return start + dash if label[:dash].lower() in _TLDS else -1
        if label.lower() not in _TLDS:
            return -1
        if end < len(text) and text[end] == ":":
            port = _DIGITS_RE.match(text, end + 1)
            if port is not None and _ends_cleanly(text, port.end()):
                return port.end()
        return end if _ends_cleanly(text, end) else -1

    def host_end(self, at: int) -> int:
        """Where a host name that starts at ``at`` ends, or -1 if none starts there."""
        text = self.text
        if at >= len(text) or not (_is_word(text[at]) or text[at] == "-"):
            return -1
        index = bisect_right(self.starts, at) - 1
        if not self.dotted[index]:
            return -1
        return self.best[index + 1]


def _match_end(pattern: re.Pattern[str], text: str, at: int) -> int:
    """End of ``pattern``'s match at ``at``, for a pattern the caller knows matches there."""
    match = pattern.match(text, at)
    assert match is not None, f"{pattern.pattern!r} must match at {at}"
    return match.end()


def _ends_cleanly(text: str, at: int) -> bool:
    """bleach's ``(?!\\.\\w)\\b`` after a top-level domain or port."""
    if at >= len(text):
        return True
    if _is_word(text[at]):
        return False
    return not (text[at] == "." and at + 1 < len(text) and _is_word(text[at + 1]))


def _after_scheme(hosts: _Hosts, at: int) -> int:
    """Where an address ends after a recognised ``scheme:``, or -1.

    Up to three slashes, then optional ``user:password@`` or ``user@``, then the host.
    """
    text = hosts.text
    cursor = at
    while cursor < len(text) and cursor - at < 3 and text[cursor] == "/":
        cursor += 1
    user = _WORD_RE.match(text, cursor)
    if user is not None:
        after = user.end()
        if after < len(text) and text[after] == ":":
            password = _WORD_RE.match(text, after + 1)
            if password is not None and password.end() < len(text) and text[password.end()] == "@":
                end = hosts.host_end(password.end() + 1)
                if end >= 0:
                    return end
        elif after < len(text) and text[after] == "@":
            end = hosts.host_end(after + 1)
            if end >= 0:
                return end
    return hosts.host_end(cursor)


def _address_end(hosts: _Hosts, start: int) -> int:
    """Where an address that starts at ``start`` ends, before any path, or -1."""
    text = hosts.text
    if _is_word(text[start]):
        word_end = _match_end(_WORD_RE, text, start)
        if word_end < len(text) and text[word_end] == ":" and text[start:word_end].lower() in _RECOGNISED_SCHEMES:
            end = _after_scheme(hosts, word_end + 1)
            if end >= 0:
                return end
    return hosts.host_end(start)


def _trim(fragment: str) -> tuple[str, str, str]:
    """Split brackets and trailing punctuation off a match: ``(prefix, address, suffix)``.

    bleach's ``strip_non_url_bits``, in a single pass.
    """
    head, tail = 0, len(fragment)
    opens = fragment.count("(")
    while head < tail:
        if fragment[head] == "(":
            head += 1
            opens -= 1
            if head < tail and fragment[tail - 1] == ")":
                tail -= 1
            continue
        last = fragment[tail - 1]
        if (last == ")" and opens == 0) or last in ",.":
            tail -= 1
            continue
        break
    return fragment[:head], fragment[head:tail], fragment[tail:]


def _render_attrs(attrs: dict[str, str]) -> str:
    return "".join(f' {name}="{value}"' for name, value in attrs.items())


def _link(address: str, harden: LinkHardener) -> str:
    """An auto-link for ``address``, or the address as plain text when its scheme is not linkable."""
    if address.startswith("-"):
        return address
    scheme = _SCHEME_PREFIX_RE.match(address)
    if scheme is None:
        href = f"http://{address}"
    elif scheme.group(1).lower() in _UNLINKED_SCHEMES:
        return address
    else:
        href = address
    return f"<a{_render_attrs(harden({'href': href}))}>{address}</a>"


def linkify_text(text: str, harden: LinkHardener) -> str:
    """Link every address in one escaped text run (no tags in it)."""
    hosts = _Hosts(text)
    pieces: list[str] = []
    resume = 0
    search = 0
    while (candidate := _START_RE.search(text, search)) is not None:
        start = candidate.start()
        end = _address_end(hosts, start)
        if end < 0:
            search = start + 1
            continue
        if end < len(text) and text[end] in "/?":
            end = _match_end(_PATH_RE, text, end + 1)
        while start > resume and text[start - 1] == "(":
            start -= 1
        prefix, address, suffix = _trim(text[start:end])
        pieces += [text[resume:start], prefix, _link(address, harden), suffix]
        resume = search = end
    pieces.append(text[resume:])
    return "".join(pieces)


def linkify(html: str, harden: LinkHardener) -> str:
    """Harden every ``<a>`` in sanitized HTML and link every bare address outside one.

    Args:
        html: ``nh3`` output (see the module docstring for why that matters).
        harden: Sets the attributes every anchor carries, the author's and the new ones alike.

    Returns:
        The HTML with anchors hardened and addresses in text linked.
    """
    parts = _TAG_RE.split(html)
    in_link = False
    for index in range(1, len(parts), 2):
        tag = parts[index]
        if tag == "<a>" or tag.startswith("<a "):
            in_link = True
            parts[index] = f"<a{_render_attrs(harden(dict(_ATTR_RE.findall(tag))))}>"
        elif tag == "</a>":
            in_link = False
        if not in_link and parts[index + 1]:
            parts[index + 1] = linkify_text(parts[index + 1], harden)
    if parts[0]:
        parts[0] = linkify_text(parts[0], harden)
    return "".join(parts)
