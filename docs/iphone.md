# Sylvie on the iPhone, Watch and AirPods

Your Mac does all the work (memory, web, actions); the phone only sends what you say and speaks the answer.

## 1. Private tunnel: Tailscale (decision 8 — default, you can change it)

Why Tailscale: it's an encrypted, private network between your own devices; works at home and away; no router setup.
Its coordination server sees which devices exist and when they connect (metadata), never the content.
The fully self-run alternative is plain WireGuard, which needs a port forward on your router.

1. Install Tailscale on the Mac and on the iPhone (App Store), sign in with the same account on both.
   (Claude can't do this part: it needs your account and a macOS VPN approval.)
2. Make sure Sylvie is running, then on the Mac:
   ```
   make phone NAME="Lea's iPhone"
   ```
   This does everything else:
   - publishes Sylvie's phone gateway on your tailnet only (`tailscale serve`). Sylvie still listens only on 127.0.0.1, and it never uses `tailscale funnel`, which would make it public.
   - puts your Mac's tailnet name (e.g. `lea-mac.tail1234.ts.net`) in `config.yaml → phone.allowed_hosts`
   - pairs the phone and prints its token **once**, plus the URL for the Shortcut. Unpaired devices are rejected.
3. Restart Sylvie.

## 2. Pairing more devices later

```
make pair NAME="Lea's iPad"
```

## 3. "Hey Siri, Sylvie" Shortcut (hands-free, also on Watch, AirPods, CarPlay)

In the Shortcuts app on the iPhone, create a shortcut named **Sylvie**:

1. **Dictate Text** (Language: Automatic; Stop listening: After Pause)
2. **Get Contents of URL**
   - URL: `https://lea-mac.tail1234.ts.net/phone/ask`
   - Method: POST
   - Headers: `Authorization` = `Bearer <your token>`
   - Request Body: JSON → `text` = *Dictated Text*
3. **Get Dictionary Value** → key `spoken` from *Contents of URL*
4. **Speak Text** → *Dictionary Value*
5. Optional: **Show Result** with key `answer` (the full answer).

Error handling: wrap step 2 with **If** *Contents of URL* has no value → **Speak Text** "I can't reach your Mac right now. Is it asleep or offline?"

Now say "Hey Siri, Sylvie" and talk after the beep. Conversations continue: if you ask again within 5 minutes, Sylvie remembers what you were talking about (on any device).

## 4. Mac availability with the lid closed (decision 13 — your choice)

The Mac must be plugged in and awake. Options (you set one up yourself):
- **Amphetamine** (free, App Store): enable "closed-display mode" only when on power.
- Or Terminal: `sudo pmset -c disablesleep 1` (on charger only; undo with `sudo pmset -c disablesleep 0`).
Also turn on System Settings → Battery → Options → "Wake for network access".
Never use these in a bag.

## Coming next

- The Sylvie iPhone app (Action Button, streaming voice both ways) and the optional room listener.
