# Hall requests still answered with the generic zeroed reply

From `loginserver.log`. Each one gets "success + 32 zero bytes" today. Most are harmless,
but the panels behind them show empty or zero. `(14,14057)` is skipped entirely because a zeroed
reply crashes the client.

Next step for each: find the observer's `OnBegin` (`tools/handler.py <Game.dll> <Observer>`), read the
reply layout, and answer with real data.

## Character / progress
- [ ] (13,13047) CS_CanFetchMoney
- [ ] (14,14004) CGS_CharacterQuickSuite
- [x] (14,14010) CG_Character_Get_Exploit
- [ ] (14,14034) UpdateRankUpgrade
- [x] (14,14040) CGS_GetAllMilitaryRank
- [ ] (14,14050) CGS_GetCharacterAllBattleFrontContribution
- [ ] (14,14054) CGS_CGSManager_CommandGradeTable
- [x] (14,14057) CGS_GetCharacterTodayMilitary
- [ ] (14,14059) GetCharacterHelpLog
- [ ] (14,14051) CGS_GetCharacterbattleFrontContributionByID

## Medals / badges
- [x] (14,14007) CGS_UpdateMyMedal
- [x] (14,14019) CGS_UpdateAllMedalInfo
- [x] (14,14027) CGS_UpdateAllBadgeInfo
- [x] (14,14028) CGS_UpdateMyBadge (Info window reads u16 id | u8 grade | u32 progress)
- [x] (14,14003) CGS_GetCharacterLog (Event Log, paged)
- [x] (14,14056) CGS_GetCharacterMilitarySuccesses (Battle tab; u8 1 battlefield / 2 arena)

## Talents
- [x] (14,14042) CGS_UpdateTalentInfo
- [x] (14,14043) CGS_UpdateCharacterRemainTalentPoint
- [x] (14,14044) CGS_UpdateCharacterTalentPoint
- [x] (14,14058) CGS_CharacterLearnTalentCost
- [x] (14,14047) CGS_CharacterLearnTalent
- [ ] Forget Talent (reset) request
- [x] Spawn as the last robot talent learned (parts from data/flyweights/Robot; verified Scout 1313 in game; MW_ROBOT=<fw> forces one)
- [ ] Heavy robots (1301 ArmorRobot, 0 equipment slots) crash the client -- weapons are armor parts; falls back to 1311 for now

## Corps / teams
- [ ] (15,15022) UpdateCorpInfo
- [ ] (15,15023) UpdateTeamInfo
- [ ] (15,15074) UpdateAllTeamFlag
- [ ] (15,15093) GetAllUpGradeTeamMemberLimitPrice

## Battlefield / campaign
- [ ] (17,17079) BF_UpdateMyHeadQuarter / UpdateCorpBasement
- [ ] (17,17099) BF_UpdateCityLink
- [x] (17,17110) GetOpenedCampaignInfo

## Tasks / announcements / social
- [ ] (20,20116) Task_GetHoldTask
- [x] (21,21047) UpdateAllAnnouncement
- [ ] (21,21048) CS_GetTaskServerAfficheList
- [ ] (21,21069) CS_AckTradeRes
- [x] Friend and block lists, real client 2026-11-01: add, delete and block from the name menu (right click), lists come back
      after a server restart, blocked lines stop. Not known: what the list's state value means (the server sends 1 for online)

## Shop / items / money
- [x] (24,24006) GS_UpdateItemInUse
- [x] (24,24008) GS_RequestAllGoodsFlyweight
- [x] (24,24010) GS_UpdateGoodsInSell
- [x] (24,24019) GS_UpdateCharacterMoney
- [x] (24,24020) GS_UpdateCharacterPoint
- [x] (24,24002) GS_UpdateItemFlyweight
- [x] (24,24009) GS_RequestGoodsFlyweight
- [x] (24,24021) GS_RequestBuyGoods
- [x] (24,24011) GS_RequestMyGoods
- [x] (24,24003) GS_RequestMyActivedGoods
- [x] (24,24015) GS_RequestActiveGoods
- [ ] (24,24028) GS_RequestSuiteInSelling (Package tab)
- [ ] Equipping bought items (24006 in-use list, loadout)

## Other open items
- [x] Anti-addiction banner in the hall
- [x] "Waiting to enter arena." popup = GAMEENGINE_DATAUNNORMAL (bad reply data); gone once shop replies were real
- [ ] Glyph before nameplate names (probably the rank badge; confirm)
- [ ] Untested: dummy misses aimed at the ground, fixed spawn point, no damage after death, random single-use gate session keys (login path!)

## Security / robustness (2026-10-20, offline)
- [x] tools/fuzz_handlers.py: every gate handler survives random bodies (0 failures)
- [x] tools/fuzz_game.py: FDP layer + game dispatcher survive random datagrams/messages (0 failures)
- [x] tools/test_login.py: passwords (scrypt, accounts.json), auto-register switch MW_AUTO_REGISTER, 5 fails/min/IP throttle,
      account name check, nothing handled before login, 30 s auth timeout, 1024 TCP connection cap
- [x] Character create: needs login, max 4 per account, malformed name rejected instead of dropping the connection
- [x] FDP: 512 session cap with 60 s idle expiry; UDP loop catches handler errors
- [x] Refusals use the client's own codes (Language/ErrorInfoENG.xml): 10001 bad login, 10002 locked, 2401x shop...
- [x] Shots are checked on the game server (2026-10-30, offline): the muzzle must be within 15 units of the robot, the
      target within the weapon's range (WP1Range + 25%), and pulling the trigger again cannot beat the fire rate.
      Real client, 2026-10-31: kills count, no false "shot from". WP1Range is in map units: a Viper shot at open sky
      aims exactly 200.0 away. Melee weapons have WP1Range 2, so MIN_RANGE keeps them able to hit (no melee weapon tried)
- [ ] Not stopped: aim bots, shots through walls (needs the terrain on the server), teleporting or speed hacks
- [x] Batched saves: stats/log lines flushed every 5 s (atomic writes); purchases/talents/creates save at once
- [x] MW_DEBUG gates packet traces; AES and session keys never logged
- [x] MW_PUBLIC_IP sets the gate/game address sent to clients (was hardcoded 127.0.0.1)
- [x] server/README.md for people running the server

## Multiplayer (2026-10-21)
- [x] Battle ticket: QS_ApplyEnter reply's last u32 comes back as the first u32 of the FDP connect stream -> random single-use tickets map game connections to characters
- [x] Shared match per battlefield: players see each other (spawn, nameplate, health bar), shots relayed, PvP damage/kill feed/K/D/honor; verified with two clients
- [x] Players spawn 5 units apart (slot offset); MW_PASSIVE_DUMMIES=1 for PvP tests
- [x] Walking: robots had speed 0 -- GS_Robot_Skill (19,50132) u32 robot | f32 speed | f32 up speed | vec3 velocity now sent on spawn/respawn
- [x] Movement relay verified both ways with two clients (40100 steer flags + 40130 positions -> 50100)
- [x] Rounds (tools/test_match.py): a Mars Front round ends after MW_ROUND_SECONDS or MW_ROUND_KILLS player kills. Most
      player kills gets a win, and everyone gets the Record window (19,30150). Real client 2026-11-01: the timer ending, the
      warning and end messages (19,30250, big red text), the Record window, and back to the lobby after 20 s. Not tried:
      the kill limit, a real win, or Quick Enter straight into a new round
- [ ] Teams (all players are on their own side now), respawn point choice. Needs a client session: without knowing what
      sets the nation, players could not tell the sides apart, so nothing is built yet. Client's Kill arena, read from Game.dll, not
      tried: arena logic 5 in (19,40050), then init data u8 nation | u8 nation | f32. (19,49000) f32 seconds | u8 | f32
      = countdown, (19,52001) u8 nation | i32 = score of a side ("Our Force" / "Enemy Force"), (20,20130) u32 n |
      n x 6 str = score table. The local character's nation (+0x30) picks the sides; 0x10 is one of them. Not read yet:
      what sets +0x30, and whether a catalog 20 message reaches the client on the game session
- [ ] Hit detection is generous (HIT_RADIUS 6 around the robot or the client's ray end point): shots at the ground next to a player count
- [x] Test tooling: tools/vinput.py drives a client's DirectInput keyboard/mouse (even minimized); tools/quickenter.js presses Quick Enter; MW_FORCE_BF=1 sends every Quick Enter to one battlefield. The client saves LastAccount after login, so set Welkin4591.ini right before each launch.

## Admin / moderation (2026-10-29, tested offline)
- [x] Kick: closes the player's gate connection and game session, and takes their robot out of the match
- [x] Ban / timed ban per account (bans.json): refused with the client's own texts -- 12001 "Account has been
      locked, please contact GM." (permanent), 12011 "Account being punished, ..." (timed). Checked after the
      password, so a ban never tells strangers that an account exists
- [x] IP and network ban (1.2.3.4, 1.2.3.0/24): 10003 "IP is locked, please contact GM." at login; gate and game
      connections from it are dropped
- [x] Reason, who banned, expiry; unban; ban list
- [x] Server console (type commands in the server window) and tools/admin.py (127.0.0.1 only, needs admin.key)
- [x] Every admin command goes to admin.log
- [x] Roles per account (player / gm / admin, stored in accounts.json); nothing uses them yet
- [x] A second login of the same account closes the first one
- [x] GM commands in chat (2026-10-31, offline): a line starting with ! runs on the login server, where the roles are.
      GM: who, matches, kick, ban, unban, bans. Admin also: ipban, unipban, role, backup. Replies come as private lines
- [x] Chat, real client 2026-11-01: gate pushes show. The hall's Lobby tab sends (21,21513) and shows as [Broadcast]. The
      client allows one Lobby line or whisper about every 60 s, and shows no message when a line is held back. Hall whispers
      are (21,21051), not 21032. The battle box's Country tab is (21,21004): u16 5 | str, shown from (21,21204), and the
      client does not show its own line. Country reaches the player's match: game servers report joins and leaves to the
      gate, because shown lines go out on the gate. (21,21211) did not show in the battle box
- [ ] Team tab and hall Division Channel: team chat (21,21063) and other (21,21004) channels are not read in game. Whispers
      from the battle box are not tried. Enter in battle needs DirectInput: tools/vinput.py enter:0.2 sends a line
- [ ] The client has its own / GM commands when the character's GM byte is set: (13,13041) and others, not read yet
- [x] Web admin page (tools/webadmin.py, 2026-10-30): one file, sends admin commands, local only; README explains
      the admin port so people can build their own. Login and an IP ban tried in a real browser
- [x] Checked in game 2026-10-30: kick drops the client to the login screen with no message; timed ban, permanent
      ban and IP ban each show their text at login; login works again after unban
- [ ] Tell a kicked player why (2026-11-02, fake clients only; tools/test_chat.py): kick and ban send a whisper from Server,
      then close after 1.5 s. Try in game, also from a battle, where the whisper tab has not been seen working.
      GMKickRes (13,13041) is only the reply to a GM ("Kick %s Success!")
- [x] A kick now closes the socket as well: on Windows shutdown does not wake a blocked recv, so a client that ignored it
      stayed on the gate. A kicked player can still act for the 1.5 s of the notice, but can not chat
- [ ] DisconnectByServer (19,30150, f32 seconds) ends the battle for that player: Record window with a countdown,
      then back to the lobby, no crash (in game 2026-10-30). No reason text. Use it for match end, or to remove a
      player from a battle but keep them in the hall

## Disconnects (2026-10-29, in game 2026-10-30)
- [x] The client sends an FDP close packet (system, split id 4) when it leaves a battle: the player leaves at once
      (before: only after 15 s of silence)
- [x] Gate connection closed (client quit), logged in again, kicked, banned, new connection from the same client:
      the old game session closes
- [x] Others are told: robot shown destroyed, then GS_Object_Detach_Object (19,50002, u32 object id) and
      GS_Character_Delete_Character (19,30002, u32 char id). In game: 7 fake players left a real client's match, the
      client's robot list dropped them and it did not crash. If it ever does, set REMOVE_LEFT_PLAYERS = False
- [ ] Watch a robot vanish on screen, and leave with a real second client (only fake players tried so far)
- [x] Time played is saved when a player leaves

## Split servers (2026-10-30, real client 2026-10-31)
- [x] MW_ROLE = login, gate or game; start_split.py runs them on one machine; tools/test_split.py checks them
- [x] Login signs the session key with Ed25519, a gate holds only the public key, each key works once. The battle
      ticket is a u32 and cannot be signed: the gate announces it to the game server, with the character
- [x] Private links (cluster.py): both ends prove they hold internal.key, every message is signed and numbered
- [x] Players go to the emptiest game server; stats and log lines go to the gate, and wait if it is away
- [x] who, matches, kick, ban and IP ban work across servers
- [x] Real client with 2 game servers (2026-10-31): the signed session key works, the character and stats load, a
      battle runs on the second game server
- [x] One gate per login server: each gate has its own characters.db, so a second would show different characters.
      Login refuses it. More gates need a shared character database (MariaDB) or one realm per gate
- [x] tools/test_failover.py kills and restarts login, gate and game servers: stats wait for the gate, logins and bans
      come back, a restarted server rejoins
- [x] A game server that dies: after 6 s the gate closes its players' sessions, which sends the client to the login
      screen. Real client, 2026-10-31: killed mid-battle, the client went to the login screen without a crash, logged
      back in and joined a battle on the other game server
- [x] tools/test_failover.py sometimes failed "game servers rejoin": two threads could print on one log line, and the
      test counted lines. log() now makes one write and the test counts messages
- [x] A hung server loses its link after 10 s; game servers send reports from their own thread, so the game loop never waits
- [x] A gate restart forgot used session keys: the gate now refuses keys issued before it started. A player logging
      in during the restart has to log in again
- [ ] A gate crash loses up to 5 s of stats from its memory, and reports still on the way
- [ ] Web site
- [ ] MariaDB for a server too big for SQLite on one disk (db.py is the only file that would change much)

## Storage (2026-10-30, tested offline)
- [x] db.py: SQLite. accounts.db (accounts, bans) only on login, characters.db only on the gate, none on game servers
- [x] Every query uses bound parameters; tools/test_db.py tries hostile names and checks no query is built from strings
- [x] Only what changed is written. 10000 characters saved, 800 of them playing: 49 ms per batch, a purchase 0.1 ms.
      The old JSON file took about 1 s per write at that size, before the disk
- [x] JSON files are imported once and renamed to *.imported; a broken one stops the server and is left alone
- [ ] All characters are still loaded into memory at start. Load on login if this ever matters
- [x] Admin command backup copies the databases into backups/ on each server while it runs. Copying the .db files by
      hand is only safe with the server stopped
- Load test, 2026-10-30, with SQLite: 800 of 800 fake players on 3 game servers, about 267 each: 14 ms tick (of 33), 45% of
  one core each, 6 MB/s out each (22 KB/s per player), no errors. Move delay median 32 ms, 99% 52 ms, max 87 ms. All
  800 characters reached characters.db. Bandwidth left as it is: far robots already get fewer moves
- Windows note: COMODO on the dev PC appears to delete server/tools/loadtest.py (and copies of it) shortly after it
  runs. `git restore server/tools/loadtest.py` brings it back. An exclusion for C:\dev\openwelkin should stop this

## Performance (2026-10-29)
- [x] One game loop thread for all matches at 30 ticks/s instead of a thread per match and a timer thread per shot
- [x] Events queued per player and sent once per tick, several to a packet (join: 43 messages in 16 packets)
- [x] Moves merged: each player gets only the newest position of each robot per tick
- [x] Robots more than 250 units away get position updates every 5th tick
- [x] Lost reliable packets are resent (the client repeats its ack 3 times, or 4 s pass; it acks slowly)
- [x] Full matches: a battlefield opens another match after MW_MATCH_PLAYERS (32) players
- [x] Packet hex dumps were built for every packet even with MW_DEBUG off: now skipped (about a third of tick time)
- [x] tools/loadtest.py: fake players over the real protocol. Results on the dev PC (16 threads), 30 s in battle,
      players walking, firing and pinging:

      | players | matches | tick (of 33 ms) | server CPU | out | move delay median / 99% |
      |---------|---------|-----------------|------------|-----|-------------------------|
      | 64      | 2       | 5 ms            | 18%        | 1.4 MB/s | 36 / 42 ms |
      | 256     | 8       | 11 ms           | 37%        | 5.8 MB/s | -- |
      | 400     | 13      | 17 ms           | 60%        | 8.8 MB/s | 39 / 56 ms |

      One Python process should hold about 600 players before ticks run late. Past that: run matches in more
      processes (the split-server plan), or move the game server to a faster language.
- [x] Real client on the batched server (2026-10-30): login, join, walk, fire, die, respawn all fine, one resend seen
- [ ] Bandwidth per player is about 22 KB/s at 32 per match; most of it is moves. Send fewer for robots behind or
      far away, or pack moves smaller, if hosting costs matter
