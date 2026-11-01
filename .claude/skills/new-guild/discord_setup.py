"""Make a private Discord channel, a lead role and a webhook for each new guild, then write the prod script.

    set -a; source ~/Code/plfog/.env; set +a
    python3 discord_setup.py spec.json out_dir/

spec.json is a list of {"name", "channel", "topic", "about"}. Idempotent: an existing role or channel of
the same name is reused, never duplicated. Writes out_dir/discord_out.json and out_dir/create_guilds.py.
"""

import json
import os
import sys
import urllib.request

TOKEN = os.environ["DISCORD_BOT_TOKEN"]
SERVER = "933589656996565023"  # also the @everyone role id
API = "https://discord.com/api/v10"
BOT_USER = "1520295376727572661"
GUILDS_CATEGORY = "936437688364597260"
VIEW, SEND, HIST, EMBED, ATTACH, REACT, WEBHOOKS = 1024, 2048, 65536, 16384, 32768, 64, 1 << 29
LEAD_ALLOW = VIEW | SEND | HIST | EMBED | ATTACH | REACT


def call(method: str, path: str, body: object = None) -> object:
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Authorization": f"Bot {TOKEN}",
        "Content-Type": "application/json",
        "User-Agent": "plfog-admin (pastlives.space, 1)",
    }
    with urllib.request.urlopen(urllib.request.Request(API + path, method=method, data=data, headers=headers)) as r:
        return json.loads(r.read() or "null")


def main(spec_path: str, out_dir: str) -> None:
    spec = json.load(open(spec_path))
    roles = {x["name"]: x for x in call("GET", f"/guilds/{SERVER}/roles")}
    channels = {x["name"]: x for x in call("GET", f"/guilds/{SERVER}/channels")}
    # A bot can only grant permissions it holds; fail before creating anything.
    assert LEAD_ALLOW | WEBHOOKS == (LEAD_ALLOW | WEBHOOKS) & int(roles["Fog Bot"]["permissions"])
    color = roles["Woodworkers Guild Lead"]["color"]
    rows = []
    for g in spec:
        role_name = f"{g['name']} Lead"
        role = roles.get(role_name) or call(
            "POST",
            f"/guilds/{SERVER}/roles",
            {"name": role_name, "permissions": "0", "color": color, "mentionable": True},
        )
        # Private: @everyone denied, no Member grant. Admins see it through Administrator.
        channel = channels.get(g["channel"]) or call(
            "POST",
            f"/guilds/{SERVER}/channels",
            {
                "name": g["channel"],
                "type": 0,
                "parent_id": GUILDS_CATEGORY,
                "topic": g["topic"],
                "permission_overwrites": [
                    {"id": SERVER, "type": 0, "allow": "0", "deny": str(VIEW | SEND)},
                    {"id": role["id"], "type": 0, "allow": str(LEAD_ALLOW), "deny": "0"},
                    {"id": BOT_USER, "type": 1, "allow": str(VIEW | SEND | HIST | WEBHOOKS), "deny": "0"},
                ],
            },
        )
        hooks = call("GET", f"/channels/{channel['id']}/webhooks")
        hook = (
            hooks[0]
            if hooks
            else call("POST", f"/channels/{channel['id']}/webhooks", {"name": "Past Lives Member Portal"})
        )
        print(g["name"], "role", role["id"], "channel", channel["id"], "#" + channel["name"])
        rows.append(
            {
                "name": g["name"],
                "about": g.get("about", ""),
                "discord_channel_id": channel["id"],
                "discord_channel_name": "#" + channel["name"],
                "discord_url": f"https://discord.com/channels/{SERVER}/{channel['id']}",
                "discord_webhook_url": f"https://discord.com/api/webhooks/{hook['id']}/{hook['token']}",
                "lead_role_id": role["id"],
            }
        )
    os.makedirs(out_dir, exist_ok=True)
    json.dump(rows, open(os.path.join(out_dir, "discord_out.json"), "w"), indent=1)
    template = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "create_guilds.py.tmpl")).read()
    portal_rows = [{k: v for k, v in r.items() if k != "lead_role_id"} for r in rows]
    open(os.path.join(out_dir, "create_guilds.py"), "w").write(template.replace("ROWS = []", f"ROWS = {portal_rows!r}"))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
