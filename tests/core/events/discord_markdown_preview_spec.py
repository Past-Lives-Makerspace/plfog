"""``discord_markdown_html``: an embed description rendered the way Discord shows it, for a preview."""

from __future__ import annotations

from django.utils.safestring import SafeString

from core.events.discord import discord_markdown_html


def describe_discord_markdown_html():
    def it_renders_inline_code_monospace_and_bold_as_strong():
        html = discord_markdown_html("🥇 `███░` **Metal Guild**: $600.00 (60.0%)")
        assert html == (
            '🥇 <code class="pl-discord-preview__code">███░</code> <strong>Metal Guild</strong>: $600.00 (60.0%)'
        )
        assert isinstance(html, SafeString)

    def it_escapes_everything_typed_before_adding_markup():
        assert discord_markdown_html("**B&W <Darkroom>** <script>x</script>") == (
            "<strong>B&amp;W &lt;Darkroom&gt;</strong> &lt;script&gt;x&lt;/script&gt;"
        )

    def it_leaves_plain_text_and_line_breaks_alone():
        assert discord_markdown_html("Hello\n\nworld") == "Hello\n\nworld"

    def it_leaves_a_lone_asterisk_or_backtick_as_typed():
        assert discord_markdown_html("5 * 3 and a ` tick") == "5 * 3 and a ` tick"
