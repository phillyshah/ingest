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

This only reads. It installs nothing, starts nothing, stops nothing, and does not touch your websites.

---

## Part C — Install the application

Do this only after I have confirmed the report looks as expected.

### C1. See the plan first, change nothing

In the same browser terminal:

```
curl -fsSL https://raw.githubusercontent.com/phillyshah/ingest/main/deploy/install.sh -o install.sh && sudo bash install.sh
```

This is a **dry run**. It prints what it would do, line by line, and changes nothing. Every line starts with
"would run". Send me the output if anything looks surprising.

If it stops with a red `fail` line, that is the script protecting you. Send me the message.

### C2. Do it for real

```
sudo bash install.sh --apply
```

It will ask you to paste two things directly into the terminal window:

- the same Supabase connection string from step A1
- your Supabase project URL, which looks like `https://abcdefgh.supabase.co` and is on the Supabase **Project
  Settings → API** page

Typing them here rather than sending them to me keeps them on your own server. The script also generates its own
signing secret, so you never have to invent one.

What it does, in order: creates a locked-down folder, downloads the code, starts the application on the server's
internal-only ports, then adds **one new website entry** for `ingest.phillyshah.com`. It checks the web server
configuration before switching it on, and if that check fails it undoes its own change and leaves your other sites
exactly as they were.

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

If the install said nginx, paste this and follow its prompts, choosing to redirect HTTP to HTTPS when asked:

```
sudo certbot --nginx -d ingest.phillyshah.com
```

If it said Caddy, do nothing. Caddy fetches the certificate by itself within a minute or two.

### D3. Prove it all works

1. Open `https://github.com/phillyshah/ingest/actions`.
2. Click **Verify deployment**, then **Run workflow**, then the green button.

A green tick means: the site is up, the API correctly refuses unauthenticated visitors, the page loads, no secret
is leaking into the browser, the certificate is valid, **and your other websites still work.** That last check is
deliberate. Run this workflow any time you want reassurance; it changes nothing.

---

## Part E — Give people access

Signing up in Supabase grants nothing on its own. Access is invitation only, on purpose.

For each person, in Supabase:

1. **Authentication → Users → Add user**, with their email.
2. Copy the user's **UID**.
3. Go to **SQL Editor** and run this, replacing the two values:

```sql
update app_user
   set auth_user_id = 'PASTE-THE-UID-HERE'
 where email = 'their@email.com';
```

If that email is not in `app_user` yet, create them first:

```sql
insert into app_user (tenant_id, email, display_name, roles, auth_user_id)
select id, 'their@email.com', 'Their Name', '{pt}', 'PASTE-THE-UID-HERE'
  from tenant limit 1;
```

Change `{pt}` to the role you want: `{pt}` for a physical therapist, `{clinical_lead}` for the person who signs off
clinical content, `{source_admin}` for someone managing ingestion campaigns, `{rights_reviewer}` for licensing.

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
