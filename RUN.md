# How to run the Tamil clip desk

The clip desk finds a trending GTA 6 gameplay video every few hours and cuts face-free vertical clips with a Tamil
hook. For each clip it writes a post pack: a Tamil voice-over summary, YouTube Shorts titles, a caption and hashtags.
It sends the video and the pack to Telegram, then deletes the video. You control everything from a web admin page.

There are two ways to run it:

- **[On a Linux server with Docker](#1-linux-server-docker-recommended)** (recommended): it runs around the clock and restarts by itself.
- **[On a Mac without Docker](#2-mac-without-docker)**: good for testing. Clips render faster thanks to the Apple GPU.

You need:

- a **Claude account** (Pro or Max) or an Anthropic API key, because every run uses Claude Code;
- a **Telegram bot** (create one with [@BotFather](https://t.me/BotFather)) and the chat or channel that should receive the clips.

---

## 1. Linux server (Docker, recommended)

### Install Docker (once)

```bash
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER
```

Log out and back in, so you can run `docker` without `sudo`.

### Get the code and start it

```bash
git clone https://github.com/shkshariq/gta-vi-clipper.git ~/gta-vi-clipper
cd ~/gta-vi-clipper
./run.sh docker up        # builds the image (a few minutes the first time), starts the admin UI and the scheduler
./run.sh docker login     # log in to Claude once: open the link it shows, then type /exit
./run.sh docker status    # shows the admin address and the one-time setup code
```

Run everything as your normal user, not with `sudo`.

> **Can't log in through a link?** Run `claude setup-token` on a computer where you are logged in to Claude.
> Then create a file called `.env` next to `docker-compose.yml` containing `CLAUDE_CODE_OAUTH_TOKEN=<the token>`,
> and run `./run.sh docker restart`.

### Open the admin page

1. Allow **TCP port 8765** in the server firewall. On a cloud server, do this in the provider's firewall or security
   group. If the server uses ufw, run `sudo ufw allow 8765/tcp`.
2. Open `http://<server-ip>:8765` in your browser. Use the public IP if it is a cloud server.
3. **First visit:** enter the **setup code** from `./run.sh docker status`, then choose your admin username and
   password. The code works only once. After that you sign in with the username and password.

### Set it up in the admin page

| Where | What to do |
|---|---|
| **Settings → Telegram** | Paste the bot token, then click **Detect chat id**. Click **Send test message** to check it. To post to a **channel**, first add the bot as an admin of the channel with **Post messages** turned on, and post once in the channel. |
| **Settings → Scheduler** | Set **From**, **To** and **Every** (for example 20:00 → 05:00, every 2 h), click **Save times**, then **Turn schedule on**. |
| **LLM rules** | Set **Font** to `Noto Sans Tamil`. Here you can also change the niche, the search queries, which videos get picked, the language, and the style of the voice-over, titles and caption. Changes apply from the next run. |

Click **Run now** on the Dashboard, or run `./run.sh docker now`, to test it straight away.

### Everyday commands

```bash
./run.sh docker status    # containers, schedule, whether a run is going
./run.sh docker now       # start one run right now
./run.sh docker log       # watch the current run live (Ctrl+C stops watching, not the run)
./run.sh docker logs      # container logs (admin UI + scheduler)
./run.sh docker shell     # a shell inside the container
./run.sh docker down      # stop everything
```

**Update to the latest code:**

```bash
cd ~/gta-vi-clipper && git pull && ./run.sh docker restart
```

### Where things are kept

| What | Where on the server |
|---|---|
| Database: Telegram settings, admin login, runs, clips, Tamil transcripts, post packs, events | `./data/state/desk.db` |
| Run logs | `./data/logs/run-<n>.log` (also shown live in the admin page) |
| Videos already used | `./data/state/done.txt` |
| Work folders (videos are deleted after a successful send, text files stay) | `./data/jobs/` |
| Claude login | Docker volume `claude-config` |

To back up, copy the `data` folder. To start over completely, run `./run.sh docker down`, then delete `data`.

---

## 2. Mac (without Docker)

```bash
git clone git@github-personal:shkshariq/gta-vi-clipper.git repo     # or the https URL
cd repo
./run.sh setup            # installs python@3.12 + ffmpeg-full with Homebrew, the Python env and the desk folder
claude                    # log in to Claude once (needs the Claude Code CLI), then /exit
./run.sh                  # starts the admin UI and opens http://127.0.0.1:8765
```

The desk folder (database, logs, jobs) is created next to the repo as `../desk`. The scheduler uses macOS launchd and
is switched on and off in **Settings → Scheduler**. On a Mac the font `Tamil Sangam MN` is used by default.

```bash
./run.sh status | now | log | restart | stop
```

---

## How a run works

1. Searches YouTube with the queries from **LLM rules** and picks one new gameplay video, ranked by views per hour.
   Videos already used are skipped.
2. Finds the best moments: chat spikes, the "most replayed" heatmap and the transcript. A long stream is cut to a
   window first.
3. For each clip, it crops out the streamer's face cam and burns in a short Tamil hook. It writes the Tamil transcript
   and the post pack, then checks frames to make sure no face is visible.
4. It sends **the video and the pack .txt** to Telegram, deletes the video files, and logs everything in the database.

You record the voice-over yourself in your own editor, using the summary in the pack.

Fixed rules: never show the streamer's face, never synthesize or clone a voice, always credit the creator, and send
nowhere but your Telegram.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| `./run.sh: cannot execute: required file not found` | You have an old copy. Run `git pull`. `run.sh` now uses bash. |
| `Docker not found` / `permission denied ... docker.sock` | Install Docker (see above) and run `sudo usermod -aG docker $USER`, then log out and back in. |
| Admin page does not load | Open port 8765 in the firewall or security group. Check that both containers are `Up` with `./run.sh docker status`. |
| `Wrong setup code` | Use the code shown by `./run.sh docker status`. It is also in `./run.sh docker logs`. |
| `Too many wrong attempts` | Five wrong passwords lock that IP for 15 minutes. Wait, then try again. |
| Run status `no_source`, or the search finds 0 videos, or "Sign in to confirm you're not a bot" in the log | YouTube is blocking the server's IP address. Server IPs often get blocked. Running yt-dlp with browser cookies fixes it. |
| Run fails right at the start with a Claude login error | Run `./run.sh docker login` again, or set `CLAUDE_CODE_OAUTH_TOKEN` in `.env`. |
| Nothing arrives in Telegram | **Settings → Send test message**. For a channel, the bot must be an admin with **Post messages** turned on. |
| Tamil hook shows boxes instead of letters | In **LLM rules**, set Font to `Noto Sans Tamil` (server) or `Tamil Sangam MN` (Mac). |
| Runs on the Mac and the server send the same clips | Each machine keeps its own `done.txt`. Turn the schedule on for only one of them. |

The page is plain http. If the server has a domain name, put HTTPS in front of it (for example with Caddy) so the
password is not sent unencrypted.
