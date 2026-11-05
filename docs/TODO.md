# Hall requests still answered with the generic zeroed reply

From `loginserver.log`. Each one gets "success + 32 zero bytes" today. Most are harmless,
but the panels behind them show empty or zero. `(14,14057)` is skipped entirely because a zeroed
reply crashes the client.

2026-11-04, offline: every reply layout below was read from Game.dll (see "Read from the client, not tried in game" in
PROTOCOL.md). All of them are safe with the zeroed reply (128 bytes); what is left is real data for them.

Next step for each: find the observer's `OnBegin` (`tools/handler.py <Game.dll> <Observer>`), read the
reply layout, and answer with real data.

## Character / progress
- [x] (13,13047) CS_CanFetchMoney: the reply's one u32 is read and dropped, nothing to do
- [x] (14,14004) CGS_CharacterQuickSuite and (14,14005) save: Robot Roof loadout presets, kept as the client's text
      and sent back. Real client 2026-11-04: two presets put in the database came back in the hall and the client
      parsed them (character +0xd8 list: id 1, robot 1311, name "Sniper"). The Robot Roof is the respawn screen in
      battle (dialog 108, CNewRobotRoofUI, Shortcut tab); there the list was empty and the tab showed 10 default
      slots, so the battle side gets them some other way (not read yet). Saving one in game needs a robot in the
      left slot, which needs robot items in the warehouse (see Equipping below). 2026-11-05, offline: the battle
      screen's LoadQuickSuite (0x105dd7f0) reads the same list (local character +0xd8), and the client asks 14004
      only from the CharacterOnline reply (0x10402510), so once per hall login. So in battle the list was emptied,
      or the local character is a new object. Next client session: compare the local character's address in the
      hall and in battle
- [x] (14,14010) CG_Character_Get_Exploit
- [ ] (14,14034) UpdateRankUpgrade
- [x] (14,14040) CGS_GetAllMilitaryRank
- [x] (14,14050) CGS_GetCharacterAllBattleFrontContribution and (14,14051): contribution = honor from kills on that
      front. Real client 2026-11-02: the Total Contribution column and Battle Joined show it; after one kill on Northern Front both show 10
- [ ] (14,14054) CGS_CGSManager_CommandGradeTable
- [x] (14,14057) CGS_GetCharacterTodayMilitary
- [ ] (14,14059) GetCharacterHelpLog: help events seen, "10100|10101|". Nothing in Game.dll sends the save
      (14,14060), so it stays empty. An empty log at rank level 1 opens the tutorial video; if that shows at
      every login, send "10100|"

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
- [x] Forget Talent, real client 2026-11-02: (14,14045) u32 character id, empty reply; points come back and the robots
      can be learned again. 14043 ends with u32 wait in MINUTES | u32 price (the confirm box shows the price, a second try
      shows "Last forget talent time is ..., you need to wait 23Hour58Mins")
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

- [x] (17,17111) asked once per joined battle in the hall (from a function next to CHallRank). Request u32 battle
      front id; reply str | str | u8. The first str goes into that front's JoinedBattleInfo (+8) and the hall's
      joined battle list is drawn again; the zeroed reply gives empty strings. Result 0x4282 removes the front from
      the list. Not known what the text is

## Tasks / announcements / social
- [ ] (20,20116) Task_GetHoldTask
- [x] (21,21047) UpdateAllAnnouncement
- [ ] (21,21048) CS_GetTaskServerAfficheList
- [x] (21,21069) CS_AckTradeRes: one u32, read and dropped
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
- [x] (24,24025) GS_RequestExpiredItem, 2026-11-05 offline: u32 character id | u8; reply u32 n | n x (u32 item id |
      u8). Answered with the activated items whose time ran out. Not tried in game. (24,24026) has an observer but no sender
- [x] (24,24028) GS_RequestSuiteInSelling (Package tab), 2026-11-05 offline: list read from Game.dll (CMarketUI::
      OnSuiteUpdated), buy (24,24029) u32 character id | u8 currency (1 Crystal) | u32 package id. The packages are the
      emulator's own (PACKAGES in loginserver.py); buying one puts each item in the warehouse. Real client 2026-11-05:
      the package id is the picture, GoodsPic_9000..9006 are gift boxes with the original names on them (9005/9006 are
      vehicle packages, not used yet); the tab lists them from the second time it is opened; Infantry Gear Package
      bought for 1000 Crystal, its four items showed in the Warehouse
- [ ] Equipping bought items (24006 in-use list, loadout). 2026-11-05, offline: (24,24007) GS_SetItemInUse read
      from Game.dll and answered: u32 character id | u32 item id | u32 slot, sent when an item is dropped in the
      hall Warehouse (slot 0 = dropped back on the list). The slot is saved with the item and 24006 sends it back.
      Not tried in game. Still open: how many slots there really are (32 sent), whether the battle Robot Roof's
      warehouse lists in-use items. In battle the weapon in the lowest in-use slot is used (else the newest
      activated one)

## Ranking
- [x] Ranking window, real client 2026-11-02: Honor and Crystal tabs show names, rank and score; your own row is
      highlighted. Row = u32 character id | str name | u8 camp (0x10 green, 0x20 red, anything else nearly black) | u32 |
      str (not read) | u8 rank level | u32 score. The client numbers rows itself. My place is counted from 0 and only
      shows under the list past place 18 (2026-11-03, 28 characters: "My Ranking 26 tester 310" under the 20 rows).
      There is no paging: the window shows 20 rows and scrolling sends nothing
- [x] Camps are nations, real client 2026-11-04: the create request carries the side picked on the Choose Nation
      screen (0x10 Federal, 0x20 Alliance), kept in the "model" column. Rows send it as the camp byte; CharacterOnline
      sends it after the id, which turns your own name under My Ranking red (Alliance) and puts the Alliance emblem
      before your name in the hall. Characters made by older scripts have none, so their names are almost black; tools/loadtest.py now
      makes bots of both nations
- [x] Weapon tab, real client 2026-11-03: (15,15108) gives the buttons, u32 n | n x (u32 kind 1 Weapon / else
      Vehicle | u32 weapon id | str name); the ten shop weapons show. (15,15102) list has the Honor layout, request
      u8 period | u32 kind | u32 weapon | u32 first row | u32 rows; (15,15103) my place, u32 character id | u8 period |
      u32 kind | u32 weapon. The emulator had read the kind as the weapon (every list came back empty); fixed. Two
      Thunder kills showed as "tester 2" under Thunder
- [ ] Vehicle tab: no vehicles yet
- [x] Request read in game 2026-11-03: u8 period (1 Total, 2 Monthly, 3 Weekly, 4 Daily) | u32 first row | u32 rows
      (20); Shooting (15100) has one more u8 in front. My place: u32 character id | u8 period. The u32 after the camp
      byte is a team id: anything but 0 makes the client ask UpdateTeamInfo (15,15023) once per row, so 0 is sent
- [x] Periods (real client 2026-11-03): honor, kills and crystal earned are also counted per day, week (from
      Monday) and month, server local time, in stats "<stat>_day" etc. next to "at_day" etc. A counter starts again
      when the next one is added in a new period. Daily, Weekly and Monthly list only characters who earned something
      in it; Crystal's Total stays the balance. In game: Daily Honor showed tester 20 and Daily Shooting tester 2
      after two kills. Shooting's request is u8 period | u8 1 | ...; the emulator had taken the 1 as the period, so
      Daily showed all-time kills; fixed (also its my-place request, u32 id | u8 period | u8 1)

## Other open items
- [x] Anti-addiction banner in the hall
- [x] "Waiting to enter arena." popup = GAMEENGINE_DATAUNNORMAL (bad reply data); gone once shop replies were real
- [ ] Glyph before nameplate names in battle: a small bracket before "Target 1" in red or white. The hall's star before
      your name became the nation emblem once CharacterOnline sent the nation, so this may be the same thing
- [ ] Untested: dummy misses aimed at the ground, fixed spawn point, no damage after death, random single-use gate session keys (login path!)

## Security / robustness (2026-10-20, offline)
- [x] tools/fuzz_handlers.py: every gate handler survives random bodies (0 failures)
- [x] tools/fuzz_game.py: FDP layer + game dispatcher survive random datagrams/messages (0 failures)
- [x] tools/test_login.py: passwords (scrypt, accounts.json), auto-register switch MW_AUTO_REGISTER, 5 fails/min/IP throttle,
      account name check, nothing handled before login, 30 s auth timeout, 1024 TCP connection cap
- [x] Character create: needs login, max 4 per account, malformed name rejected instead of dropping the connection
- [x] FDP: 512 session cap with 60 s idle expiry; UDP loop catches handler errors
- [x] Game message flood (2026-11-05, offline): each game session gets 300 messages at once, then 150 a second;
      more are dropped, and after 3000 dropped the session is closed. A 40-bot load test drops nothing
- [x] Game sessions need a live battle ticket in the SYN (2026-11-05, offline): before, any made-up SYN opened a
      session, so one machine could fill all 512 and keep them alive. Also at most 16 sessions per address (not
      for 127.x, where tools/loadtest.py runs). tools/test_match.py; load test with 40 bots still joins all
- [x] Refusals use the client's own codes (Language/ErrorInfoENG.xml): 10001 bad login, 10002 locked, 2401x shop...
- [x] Shots are checked on the game server (2026-10-30, offline): the muzzle must be within 15 units of the robot, the
      target within the weapon's range (WP1Range + 25%), and pulling the trigger again cannot beat the fire rate.
      Real client, 2026-10-31: kills count, no false "shot from". WP1Range is in map units: a Viper shot at open sky
      aims exactly 200.0 away. Melee weapons have WP1Range 2, so MIN_RANGE keeps them able to hit (no melee weapon tried)
- [ ] Not stopped: aim bots, shots through walls (needs the terrain on the server)
- [ ] Speed check (2026-11-05, offline): position reports may cover 16 units a second with 40 saved up, falling is
      free; a faster move is not relayed and the robot is put back with GS_GMSetLocation (19,33000), at most every
      0.5 s. Reports in the 1.5 s after a spawn, respawn or goto are dropped quietly (they may be from before).
      MW_MOVE_CHECK=0 turns it off; tools/loadtest.py does, its bots send the time as their height. Try in game:
      walking, jumping and falling off something high must never log "put back"; does (19,33000) really move the
      robot back?
- [x] Batched saves: stats/log lines flushed every 5 s (atomic writes); purchases/talents/creates save at once
- [x] Request flood (2026-11-05, offline): each hall connection gets 120 requests at once, then 30 a second; more
      are dropped, and after 300 dropped the connection is closed (tools/test_login.py). Equipping and loadout
      presets now wait for the 5 s batch instead of a database write each, so moving items fast costs nothing
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
      warning and end messages (19,30250, big red text), the Record window, and back to the lobby after 20 s. Real client
      2026-11-03, two clients, MW_ROUND_KILLS=1: tester killing Bravo ended the round at once, both saw "tester won the
      round" and their own Record window, both went back to the hall, tester's win was saved, and Quick Enter then
      started a new match. Only kills on players count; target robot kills do not
- [x] Sides, real client 2026-11-04: GS_Character_Set_BaseInfo's first u32 after the name is the character's nation
      (+0x30), and the robot snapshot's u32 after the owner is the robot's (+0x6c). Robots of your own nation get a white
      name, others a red one. The client still sends shots at your own side, so the server ignores them (players only;
      target dummies have no nation and fight everyone). Kill feed lines carry both nations
- [ ] Kill mode (MW_KILL_MODE=1), 2026-11-05 offline: arena logic 5, side scores (19,52001), countdown (19,49000),
      the side that reaches MW_ROUND_KILLS wins and all its players get a win (tools/test_match.py). Real client
      2026-11-05: the "Our Force" / "Enemy Force" bar and a timer show. Not yet seen: the scores counting up after a
      kill (needs two players on different sides), and the timer running (it showed 00:00 at the end of a round)
- [x] Score table (20,20130), real client 2026-11-05: a Kill mode round ends with it in place of 30150 (neither
      clears the Record window). Under "Campaign": winner, then each side's kills; then the 20 s countdown and back
      to the lobby. Only arena logics register a 20130 observer, so other battles keep 30150. Catalog 20 game
      messages have no event envelope: with one, the row count read 0 and the countdown read the event key
- [ ] Teams within a side, respawn point choice. Needs a client session: without knowing what
      sets the nation, players could not tell the sides apart, so nothing is built yet. Client's Kill arena, read from Game.dll, not
      tried: arena logic 5 in (19,40050), then init data u8 nation | u8 nation | f32. (19,49000) f32 seconds | u8 | f32
      = countdown, (19,52001) u8 nation | i32 = score of a side ("Our Force" / "Enemy Force"), (20,20130) u32 n |
      n x 6 str = score table. The local character's nation (+0x30) picks the sides; 0x10 is one of them. Not read yet:
      what sets +0x30, and whether a catalog 20 message reaches the client on the game session
- [ ] Hit detection is generous (HIT_RADIUS 6 around the robot or the client's ray end point): shots at the ground next to a player count.
      2026-11-04, offline: nothing past where the shot landed (plus HIT_RADIUS) is hit any more, so a shot into a wall or
      the ground in front of a robot misses. A shot at open sky lands at exactly WP1Range and still reaches the whole
      range. Real client 2026-11-04: shots at a dummy still hit; the client's ray goes through robots (it landed 61.5
      away behind one); a shot at a standing dummy's shoulder passed 2.2 from its center. Shots are now also checked
      against where a robot was over the last 1.5 s (clients draw walking robots up to 9 units behind). With that the
      radius could come down to about 3. 2026-11-05, offline: now a capsule, radius 3 around a vertical line from
      0.5 to 4.5 above the robot's position (was a sphere of 6 around 2.5). Shoulder, head and feet shots hit; 4 to
      the side, over the head, or into the ground beside a robot miss (tools/test_match.py). Not tried in game
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
- [ ] Team and Division chat. Real client 2026-11-03: the hall's Division Channel sends (21,21004) u16 2 | str (dropped:
      there are no divisions yet). In battle the Team and Division tabs are greyed and do not open without a team or
      division, so their channel numbers wait for teams. Whispers from the battle box are not tried. Enter in battle needs DirectInput: tools/vinput.py enter:0.2 sends a line
- [ ] The client's own / commands, 2026-11-05 offline (ChatManager::SendMessage, 0x10386600). A chat line starting
      with / is a command. GM level 101..110 in CharacterOnline sets the GM byte (+0xb8); the emulator sends 101 for
      gm and 110 for admin (the gate gets it in the signed session key; a role change counts from the next login).
      GM words: kick (13,13041) and announce / say (21,21030 / 21031) are answered as !kick and !notice; tell is the
      normal whisper; banchat (21,21008) is !mute; moveto, hideme and damage go to the game server as (9,11000)
      u32 command | argument, and only moveto is done (the battle ticket carries the GM level); god and godspeed
      only change the GM's own client; bf does nothing. Real client 2026-11-05: / commands only work in the battle
      chat box (the hall's Lobby box sends a normal Broadcast line); battle base info (19,30040) must carry the GM
      byte too, or it is cleared on entering a battle; /announce 0 Battle hello showed "Battle" as a notice: the client
      reads the text with >>, so only its first word is sent. Not tried: /kick, /banchat, /moveto. Without the GM byte the letter after / picks a squad ("Detachment") action: /a N apply to join,
      /e N enter, /L leave, /l apply to be leader, /t N set the target zone (requests not read yet), /h hides the
      screen's windows and HUD (client only). Squads need teams first
- [x] goto <account> <x> <y> <z> (GM), 2026-11-05 offline: GS_GMSetLocation (19,33000) vec3 moves the player's own
      robot; the server moves it on the others too. Works across split servers (tools/test_split.py). Not tried in
      game. Also read, not used: GS_GMCommando (19,31000) u32 robot | u32 state | u32 on (1 = no physics body,
      2 = view mode)
- [x] notice <text>, real client 2026-11-02: "[Broadcast] Server: ..." in the hall chat; in battle the same line plus big
      red text in the middle for a few seconds. announcements.txt holds the hall scrolling bar
- [ ] (21,21048) task server notice list: u32 n | n x (str | u32), asked once at hall login. 2026-11-05, offline: dialog
      0x71 is the New Lobby; each line goes to its scrolling text (CNewHallUI::AddBroadcast), the u32 probably seconds.
      UpdateAllAnnouncement (21,21047) has a second observer that fills the same scrolling text and adds GM lines
      to chat. In game: does announcements.txt show twice in the New Lobby, and in what color (the row id is passed
      where a color may be)
- [x] Web admin page (tools/webadmin.py, 2026-10-30): one file, sends admin commands, local only; README explains
      the admin port so people can build their own. Login and an IP ban tried in a real browser
- [x] Checked in game 2026-10-30: kick drops the client to the login screen with no message; timed ban, permanent
      ban and IP ban each show their text at login; login works again after unban
- [x] Tell a kicked player why: kick, ban and IP ban send a Broadcast line (21,21514) from Server, then close after 1.5 s.
      A whisper showed in battle but not in the hall, which holds whispers back for a minute. Real client 2026-11-03:
      ban in the hall and IP ban in a battle both show "[Broadcast] Server: You were banned until ...: <reason>", then
      the login screen. GMKickRes (13,13041) is only the reply to a GM ("Kick %s Success!")
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
- [x] A gate crash lost up to 5 s of stats. Game servers now number their reports and keep them until the gate says
      they are on disk (every 5 s), and send them again on a new link. characters.db keeps the last number of each
      game server run, so the gate skips any it already has. Tested by killing the gate before its save
- [ ] Web site
- [ ] MariaDB for a server too big for SQLite on one disk (db.py is the only file that would change much)

## Storage (2026-10-30, tested offline)
- [x] db.py: SQLite. accounts.db (accounts, bans) only on login, characters.db only on the gate, none on game servers
- [x] Every query uses bound parameters; tools/test_db.py tries hostile names and checks no query is built from strings
- [x] Only what changed is written. 10000 characters saved, 800 of them playing: 49 ms per batch, a purchase 0.1 ms.
      The old JSON file took about 1 s per write at that size, before the disk
- [x] JSON files are imported once and renamed to *.imported; a broken one stops the server and is left alone
- [x] Characters load when their account is used, not all at start. The gate keeps every id, account and name, and
      the last 5000 accounts used (CACHE_ACCOUNTS; online ones and ones with unsaved changes always stay). Rankings
      are read from characters.db on a second, read-only connection. 50000 characters: start 0.2 s and 11 MB (was
      17 s and 240 MB), a login load 0.4 ms, a ranking 0.4 to 0.8 s once a minute without holding up the game
- [x] A save batch held the store lock about 0.3 ms per changed character (100 ms for 300): every item, talent
      and contact was written again even when only stats changed. Now a battle stats change writes only the stats
      it changed, unless something else of that character is waiting too. 300 characters with 12 items, 15 talents
      and 40 contacts each: 16 ms a batch (was 119 ms)
- [x] Admin command backup copies the databases into backups/ on each server while it runs. Copying the .db files by
      hand is only safe with the server stopped
- Load test, 2026-10-30, with SQLite: 800 of 800 fake players on 3 game servers, about 267 each: 14 ms tick (of 33), 45% of
  one core each, 6 MB/s out each (22 KB/s per player), no errors. Move delay median 32 ms, 99% 52 ms, max 87 ms. All
  800 characters reached characters.db. Bandwidth left as it is: far robots already get fewer moves
- Windows note: some antivirus programs delete server/tools/loadtest.py (and copies of it) shortly after it runs.
  `git restore server/tools/loadtest.py` brings it back. An exclusion for the repository folder should stop this

## Performance (2026-10-29)
- [x] One game loop thread for all matches at 30 ticks/s instead of a thread per match and a timer thread per shot
- [x] Events queued per player and sent once per tick, several to a packet (join: 43 messages in 16 packets)
- [x] Moves merged: each player gets only the newest position of each robot per tick
- [x] Robots more than 250 units away get position updates every 5th tick (since 2026-11-03: see Bandwidth below)
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
- [x] Bandwidth (2026-11-03, tested offline only): a viewer gets every move of a robot within 100 units or within
      its own or the robot's weapon range (fire_range, so 1250 for a ThunderStrike sniper); 5 a second for 150
      past that, 2 a second beyond. A robot that has not changed is sent once a second (moves are unreliable, so it
      is still sent now and then). Load test, 64 players, 32 per match, spread 300, Viper (range 250): 15.9 -> 14.1
      KB/s per player, same move delay. Packed together it stays about 22 KB/s: everyone is near and moving
- [x] Mid and far robots, real client 2026-11-03 (every move forced to 2 a second, a Frida probe reading where the
      client draws two target robots 30 times a second): between moves the client walks a robot on at its body
      speed (8). Dummies walked at 4 on a sine path, so they jumped back about 2 units at every move (0.8 at 5 a
      second). They now patrol at 8, at a steady speed: no jumps but at the turns (up to 4 units at 2 a second,
      under a degree on screen at that range). Players move with their own client's speed, so they should match too
- [x] Dummies kept walking after a turn or stop, real client 2026-11-04 (Frida reads of where the client draws them,
      against the server's moves): Robot::MoveTo never applies corrections under 15 units to our remote robots (see
      GS_Robot_Action in PROTOCOL.md), the client slides a robot on about a second after a stop, and speeds it up over
      about 4 s from a start. A stopped dummy stayed up to 9 units off for good. Now a robot standing for a second is
      sent 20 units up and then in place, in one packet: from then on it is exactly where the server has it
- [ ] Walking robots are still drawn up to 9 units behind the server (the client's own speed-up). Shots use the
      1.5 s trail; a dummy that speeds up like the client would look better
