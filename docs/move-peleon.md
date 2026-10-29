# Move Peleon to another server

Moving Peleon **does not touch user credentials**. Every user key, config and
`users.json` lives on Phebes. Clients connect only to Phebes and never see
Peleon's IP or key. Nobody needs a new config, and no DNS changes.

The only link between Phebes and Peleon is the backbone tunnel. Phebes's
backbone config (`templates/wireguard/dromos-backbone-phebes.conf.j2`) has two
values from Peleon:

- `Endpoint = {{ dromos_peleon_host }}:51821` — Peleon's IP, from the inventory.
- `PublicKey = {{ dromos_peleon_public_key }}` — Peleon's backbone public key,
  read from Peleon by `bootstrap.yml`.

Peleon does not store Phebes's address, because Phebes always opens the tunnel.
It stores only Phebes's backbone public key, and that key does not change here.

So you do not have to carry over Peleon's key. The new Peleon generates its
own, and `site.yml` updates Phebes with the new key and endpoint.

Impact: only foreign (non-RU) traffic stops, and only for the few seconds while
Phebes restarts `wg-quick@dromos-backbone`. RU traffic keeps working.

## 1. Prepare the new VPS

You need Ubuntu 22.04 or 24.04 with an IP in the exit country you want, and SSH
access for a user with sudo. Open UDP 51821 inbound in the provider firewall.
The playbook opens it in UFW.

Check which interface has the default route (`ip route show default`). The
playbook detects it automatically. Set `dromos_egress_ifname=...` on the Peleon
host line only if detection picks the wrong one.

## 2. Build the new Peleon (old Peleon still in service)

Generate a separate inventory with the **new** Peleon IP. Keep the same Phebes
IP and DNS name:

```bash
scripts/dromos-init-inventory --output ansible/inventory/new-peleon.ini
```

Get Phebes's backbone public key, then deploy only the new Peleon. This does not
touch Phebes, so users keep using the old Peleon. Add `-K` to both commands if
your sudo asks for a password.

```bash
PHEBES_BB_PUB=$(ansible phebes -i ansible/inventory/prod.ini -b -m command \
  -a 'cat /etc/dromos/secrets/backbone.pub' | tail -1)
echo "$PHEBES_BB_PUB"     # 44 chars ending in '='

ansible-playbook -i ansible/inventory/new-peleon.ini ansible/playbooks/peleon.yml \
  -e dromos_phebes_backbone_public_key="$PHEBES_BB_PUB"
```

The new Peleon now waits for a handshake. Until the cutover,
`dromos-healthcheck` on it reports no backbone handshake. That is expected.

## 3. Cut over

```bash
cp ansible/inventory/prod.ini ansible/inventory/prod.ini.old-peleon
mv ansible/inventory/new-peleon.ini ansible/inventory/prod.ini
ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/site.yml
```

`site.yml` reads the new Peleon's public key, renders Phebes's backbone config
with the new endpoint and key, and restarts only `wg-quick@dromos-backbone` on
Phebes. The client-facing `wg-quick@dromos-client` is not restarted, so users
stay connected.

## 4. Verify

```bash
scripts/dromos-diagnose -i ansible/inventory/prod.ini
```

On a client, `https://ifconfig.co/ip` should now show the **new** Peleon IP,
and RU sites should still open through Phebes's IP.

## 5. Decommission the old Peleon

Leave it running for a day so you can roll back, then:

```bash
ssh -t laertid@OLD_PELEON_IP 'sudo systemctl disable --now wg-quick@dromos-backbone'
```

Then delete the VPS.

## Rollback

The old Peleon still has a valid config, because Phebes's backbone key did not
change. Restore the old inventory and redeploy:

```bash
cp ansible/inventory/prod.ini.old-peleon ansible/inventory/prod.ini
ansible-playbook -i ansible/inventory/prod.ini ansible/playbooks/site.yml
```

## Optional: keep Peleon's backbone key

You do not need to. If you want to anyway, for example to keep the Phebes
config diff to a single endpoint line, copy `/etc/dromos/secrets/backbone.key`
and `backbone.pub` to the new host before step 2. Use the same tar/scp commands
as in [rebuild-phebes.md](rebuild-phebes.md). The two Peleons then share a key,
so stop the old Peleon's backbone right after the cutover.

## If the jump hub runs on Peleon

Jump is independent of Dromos, but its state lives on the same host. Move
`etc/dromos/jump` and `etc/dromos/secrets/jump` with the tar/scp commands from
[rebuild-phebes.md](rebuild-phebes.md). Stop `wg-quick@dromos-jump` on the old
host, then run `sudo ./install.sh --endpoint <same endpoint>` from
`jump/server` on the new host. Keys and peers are kept. If the jump endpoint
was an IP and not a DNS name, every jump peer needs a new config.
