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
- [ ] (21,21522) CHAT_GetFriendListFormDB
- [ ] (21,21523) CHAT_GetBlackListFormDB

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
- [ ] Game server trusts the client's aim point for hits (cheat risk; fine for now)
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
- [ ] Teams (all players are on their own side now), leave/disconnect cleanup visuals (uses Dead), respawn point choice
- [ ] Hit detection is generous (HIT_RADIUS 6 around the robot or the client's ray end point): shots at the ground next to a player count
- [x] Test tooling: tools/vinput.py drives a client's DirectInput keyboard/mouse (even minimized); tools/quickenter.js presses Quick Enter; MW_FORCE_BF=1 sends every Quick Enter to one battlefield. The client saves LastAccount after login, so set Welkin4591.ini right before each launch.
