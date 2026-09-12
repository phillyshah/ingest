# Getting the engine live at ingest.phillyshah.com

Written to be followed without technical background. Every step is either clicking in a website or pasting one
block of text. Nothing here asks you to make a judgement call, and nothing changes your existing websites.

If a step does not look like what is described, stop and ask. Do not improvise on a server that hosts live sites.

---

## Before you start

You need two browser tabs: your GitHub repository, and your Supabase project. That is all.

One thing to understand, because it explains why the steps look the way they do: passwords and connection strings
go into **GitHub's encrypted secret store**, never into a chat message and never into a file on your computer.
Once a secret is in there, automated jobs can use it but nobody can read it back out, including me.

---

## Part A — Put the database schema into Supabase

### A1. Copy the connection string from Supabase

1. Open your Supabase project `ingest`.
2. Bottom-left, click the gear icon (**Project Settings**).
3. In the left menu click **Database**.
4. Scroll to **Connection string**.
5. Click the **Session pooler** tab. This matters; the other tabs give addresses that automated jobs often cannot
   reach.
6. Click the copy icon. You now have a long line starting with `postgresql://`.
7. It contains `[YOUR-PASSWORD]` as a placeholder. Replace that with your actual database password. If you do not
   know it, click **Reset database password** on the same page, set a new one, and use that.

### A2. Store it in GitHub

1. Open `https://github.com/phillyshah/ingest/settings/secrets/actions`.
2. Click **New repository secret**.
3. Name: `SUPABASE_DB_URL` — exactly that, capital letters and underscores.
4. Secret: paste the connection string from A1.
5. Click **Add secret**.

You will not be able to view it again afterwards. That is the point.

### A3. Run the migration

1. Open `https://github.com/phillyshah/ingest/actions`.
2. In the left list click **Migrate Supabase**.
3. Click the **Run workflow** button on the right.
4. In the confirmation box type: `migrate`
5. Leave the seed checkbox unticked for now.
6. Click the green **Run workflow**.

Wait about a minute, then click into the run. **A green tick means it worked.** Open the run summary and confirm
the verification step printed `PASSED`. That line is the proof that your database tables are not exposed to the
public internet.

If it fails, open the failed step, copy the red error text, and send it to me. Nothing is half-applied: each
migration runs inside a transaction, so a failure leaves the database as it was.

### A4. Load the starting reference data

Repeat A3, but this time tick the **seed** checkbox. This loads the medical coding tables and the three clinical
content packs. The packs are deliberately unsigned, so the system can show them but cannot prescribe from them
until a clinical lead signs them off.

---

## Part B — Let me see the server before anything touches it

Other websites are live on this machine, so I will not guess at its setup.

1. Log in to Hostinger and open the VPS.
2. Find **Browser terminal** and click it. A black window opens.
3. Paste this one line and press Enter:

```
curl -fsSL https://raw.githubusercontent.com/phillyshah/ingest/main/deploy/inspect.sh | sudo bash
```

4. It prints a report. Copy everything between `===== BEGIN MOVEAI SERVER REPORT =====` and
   `===== END MOVEAI SERVER REPORT =====` and paste it back to me.

5. Then run the second one, which reads how Traefik is set up so the new site can copy your existing
   conventions exactly:

```
curl -fsSL https://raw.githubusercontent.com/phillyshah/ingest/main/deploy/inspect-traefik.sh | sudo bash
```

Both only read. They install nothing, start nothing, stop nothing, and do not touch your websites. The second one
never prints `acme.json`, which holds the private key of every certificate on the server.

---

## Part C — Install the application

Do this only after the inspection reports have been read. They found what this server actually runs, and the
install is written to match it:

| What the server runs | Consequence for this install |
| --- | --- |
| Traefik v3.6 owns ports 80 and 443 | No nginx or Caddy config is written. Nothing is added to `/etc/nginx`. |
| Eight other sites route through Traefik on the `proxy` network | This app joins that network and publishes no ports. |
| Certificates come from Traefik's `letsencrypt` resolver by HTTP challenge | **DNS must exist before installing** — do Part D1 first. |
| `exposedByDefault: false` | The app opts in with `traefik.enable=true`, like every other site. |
| 7.8 GB RAM and **no swap** | Memory limits are set on all four containers so a spike here cannot reach the OOM killer and take out another site. |

The installer never touches `/opt/traefik`, the `proxy` network, or any other site's files. It writes only under
`/opt/sites/ingest`, matching the `/opt/sites/<name>` convention already used for hipapp, npi and the ledger.

### C1. Do Part D1 first

Certificates are issued by HTTP challenge, which means Let's Encrypt has to reach `ingest.phillyshah.com` on
port 80 at the moment the app starts. **Add the DNS record first** (Part D1 below), then come back here. The
installer checks this and refuses to continue if the name does not resolve, so nothing is half-done.

### C2. Run the installer

In the Hostinger browser terminal:

```
curl -fsSL https://raw.githubusercontent.com/phillyshah/ingest/main/deploy/install.sh | sudo bash
```

It asks for two things, typed straight into your own terminal so neither is ever sent anywhere else:

1. **The Supabase connection string** — the same one from step A1 (Project Settings → Database → Connection
   string → Session pooler).
2. **A username and password to open the site.** The whole site sits behind this one browser prompt, because the
   application does not yet have its own sign-in. The password is hashed immediately; the plaintext is never
   written to a file.

It also generates its own signing secret, so you never have to invent one.

Then it builds the four containers, starts them, and finishes by checking that the site answers **401 without a
password**. If it ever answers 200 without one, it tells you to take the site down immediately and says how.

### C3. What is actually protecting the site

Worth understanding, because it is a deliberate compromise rather than the finished design:

- One shared username and password at the door, enforced by Traefik before any request reaches the app.
- Inside, the app still uses the **development sign-in**, where you pick a role from a dropdown. That is why the
  door matters: without it, anyone could choose `clinical_lead` and approve clinical content.
- The environment is declared `staging`, not `production`, because that is what it is. The API refuses to run the
  development sign-in when told it is production, and that guard is left intact.
- Consequence: the audit trail records **which role acted, not which person**. Fine while every content pack is
  an unsigned placeholder and there is no patient data. Not fine afterwards — proper sign-in is required before
  any real clinical use.

---

## Redeploying later — the `deploy-ingest` command

Once installed, there is a single command for every future release:

```
sudo deploy-ingest
```

It pulls the latest `main`, rebuilds, restarts, and checks the API came back healthy. Your configuration and
password are never overwritten. If the build fails, nothing is restarted and the running version keeps serving.
If the new version starts but is unhealthy, it prints the exact command to roll back to the previous commit.

Variations:

```
sudo deploy-ingest --logs          # deploy, then watch the logs
sudo deploy-ingest some-branch     # deploy a branch instead of main
```

This only ever touches this app's four containers. Traefik and your other eight sites are never restarted.

---

## Part D — Point the address at the server and switch on encryption

### D1. Add the DNS records

1. In Hostinger open the DNS editor for `phillyshah.com`.
2. Add a record: type **A**, name **ingest**, value **72.62.174.193**.
3. Add a second: type **AAAA**, name **ingest**, value **2a02:4780:84::32**.
4. Save.

Wait a few minutes. To check it worked, just visit `http://ingest.phillyshah.com` in a browser. Any response at
all, even an error page, means the address now reaches your server. "Site cannot be reached" means it has not
propagated yet; wait longer.

### D2. Get the padlock

Nothing to do. Traefik requests the certificate from Let's Encrypt automatically the moment the app starts, the
same way it already did for `labelcheck.90ten.life` and the rest.

Ignore the `certbot` that is installed on this machine — it manages nothing here (`/etc/letsencrypt` is empty and
there are no renewal timers). Running it would be a mistake.

The first visit can take up to a minute while the certificate is issued. If the browser warns about the
certificate, wait a minute and reload. If it still warns after a few minutes, the usual cause is DNS not pointing
at this server yet, since the challenge needs port 80 to reach it — check with:

```
cd /opt/sites/ingest && docker compose -f infra/docker-compose.prod.yml logs --tail 50 api
docker logs traefik --tail 50 | grep -i acme
```

### D3. Prove it all works

1. Open `https://github.com/phillyshah/ingest/actions`.
2. Click **Verify deployment**, then **Run workflow**, then the green button.

A green tick means: DNS resolves, **the site refuses anyone without the password**, the API is healthy, the page
loads, no secret is leaking into the browser, the certificate is valid, **and your other websites still work.**
The last two checks are the deliberate ones. Run this workflow any time you want reassurance; it changes nothing.

The locked-door check is the one that matters most. If it ever reports the site answered without a password, take
it down immediately — the workflow prints the command.

To let it check behind the password as well, add two repository secrets (Settings → Secrets and variables →
Actions): `SITE_BASIC_AUTH_USER` and `SITE_BASIC_AUTH_PASS`. Without them it still verifies the door is locked.

---

## Part E — Give people access

Signing up in Supabase grants nothing on its own. Access is invitation only, on purpose: a Supabase account with
no invitation is turned away with "this account has no access". Giving someone access is two steps, and neither
of them needs the terminal.

### E1. Create their account in Supabase

In the Supabase dashboard for the `ingest` project:

1. **Authentication** in the left sidebar → **Users**.
2. **Add user** → **Send invitation**.
3. Type their email address and confirm.

They get an email with a link to choose a password. Nothing else is needed from them.

### E2. Give them a role

In GitHub: **Actions** tab → **Invite a user** in the left sidebar → **Run workflow**.

| Box | What to put |
| --- | --- |
| Email | the same address you invited in Supabase |
| Roles | one of the roles below, or several separated by commas |
| Name shown in the app | optional — leave blank and it uses the part before the @ |
| Tick to change roles | leave unticked (see below) |

Press **Run workflow**, then click into the run. A green tick means they can sign in.

The roles:

| Role | What it lets them do |
| --- | --- |
| `pt` | a physical therapist: review exercises, draft and approve patient plans |
| `clinical_lead` | signs off clinical content; the only role that can approve a content pack |
| `source_admin` | manages sources and ingestion campaigns |
| `rights_reviewer` | records what each source's licence permits |
| `auditor` | read-only across the system |
| `integration` | the MoveAI adapter's own service account, not a person |

Give the narrowest role that does the job. `clinical_lead` in particular carries the authority to publish clinical
content, so it should go to the clinician who is actually accountable for it and to nobody else.

**Changing someone's roles later.** Run the same workflow with the new roles and tick **replace_roles**. Without
that tick it refuses and changes nothing — that guard exists so a typo in the email box cannot quietly hand the
wrong person clinical authority.

**Common messages.** "no Supabase Auth account yet" means E1 has not been done for that address — do it and run
this again. "already exists with roles [...]" means you are changing an existing person and need the tick.

---

## If something goes wrong

**A website of yours stopped working.** The installer backs up every file it replaces. In the terminal:

```
ls /etc/nginx/sites-available/*.bak.* /etc/caddy/*.bak.* 2>/dev/null
```

Restore the relevant backup over the original, then `sudo nginx -t && sudo systemctl reload nginx` (or
`sudo systemctl reload caddy`). Then tell me.

**The new site does not respond.** Check the application is running:

```
sudo docker compose --project-directory /opt/moveai/infra ps
```

**Start over cleanly.** This removes only this application and leaves your other sites alone:

```
sudo docker compose --project-directory /opt/moveai/infra down
sudo rm -f /etc/nginx/sites-enabled/ingest.phillyshah.com /etc/nginx/sites-available/ingest.phillyshah.com
sudo nginx -t && sudo systemctl reload nginx
```

**Anything else.** Copy the red text and send it to me. Do not run commands you have not been given; this server
hosts your live websites.
