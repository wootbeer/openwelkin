# MarsWar server emulator. Copyright (C) 2026 the MarsWar emulator contributors.
# Licensed under the GNU Affero General Public License v3 or later; see LICENSE.
"""World-object snapshots as the client deserializes them (Game.dll "WriteSnapshot" virtuals, which *read* from the
BinStream when the server creates an object via GS_Object_Attach_Object (19,50001) -> FlyweightManager::NewWorldObject).

All readers are bounds-checked: fields past the end of the stream keep their defaults, but nested part snapshots
must be complete so later parts stay aligned.
"""
import struct

IDENTITY = (1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0)


def world_object(pos=(0.0, 0.0, 0.0), rot=IDENTITY, char=0, b=0, c=0, d=0, e=0, f=0):
    """WorldObject::WriteSnapshot (0x1027f460):
    vec3 position | 4x4 rotation | u32 owning character id (+0x10) | u32 nation (+0x6c, WorldObject::GetCurrentNation)
    | u32 target type (+0x70) | u8 (+0x74)
    | u32 (+0x8c) | u32 (+0x88)
    The character id matters: a robot passes its own +0x10 to its parts, and Equip::IsLocal needs an owner
    (otherwise weapons never load FPV/HUD resources and Weapon::UpdateHUD crashes).
    Target type (WorldObject::GetTargetType, not set by any constructor): 1 robot, 2 establishment, 4/8/0x10 parts
    that belong to a robot (+0xb0), 0x20 plain world object, 0x40 booty. GS_Object_Armor (19,50005) only applies
    damage to a robot whose type is 1.
    | u32 aura count, n x (u32 fw | u32 id | aura snapshot) | u32 skill fw (-1 = none) [u32 id | skill snapshot]
    """
    return (struct.pack("<3f", *pos) + struct.pack("<16f", *rot) + struct.pack("<IIIBII", char, b, c, d, e, f)
            + struct.pack("<I", 0) + struct.pack("<i", -1))


def system_part(**kw):
    """SystemPart::WriteSnapshot (0x10271020): WorldObject | u32 | u32"""
    return world_object(**kw) + struct.pack("<II", 0, 0)


def robot_body(**kw):
    """RobotBody::WriteSnapshot (0x10261ed0): SystemPart | 7 x u32"""
    return system_part(**kw) + struct.pack("<7I", *([0] * 7))


def robot_armor(**kw):
    """RobotArmor::WriteSnapshot (0x10261a10): SystemPart | 5 x u32"""
    return system_part(**kw) + struct.pack("<5I", *([0] * 5))


def robot_core(**kw):
    """RobotCore::WriteSnapshot (0x10262220): SystemPart | 2 x u32"""
    return system_part(**kw) + struct.pack("<2I", 0, 0)


def func_power(energy=5000.0, hyper=100.0, change_rate=0.25, max_energy=5000.0, can_generate=False,
               can_generate_hyper=True):
    """Func_Power::WriteSnapshot (0x102c9f20), e.g. Func_BasicCore_Energy (512001), the robot's energy pool:
    u8 (Function +0x14) | f64 change rate (+0x90) | f64 energy (+0x20, Robot::GetEnergy) | f64 hyper energy (+0x28)
    | f64 max energy (+0x30, Update clamps to it) | 3 x f64 (+0x38..+0x48) | u8 can generate energy (+0x70) | u8 can generate hyper (+0x81)
    | u8 has output (+0xac) [3 x u32 | f32 | f32]
    A reload (Weapon::FastRecruit) needs energy >= the weapon's max ammo."""
    return (struct.pack("<B7d", 0, change_rate, energy, hyper, max_energy, 0.0, 0.0, 0.0)
            + struct.pack("<BBB", can_generate, can_generate_hyper, 0))


def armor_info(hp=1000, cur=None):
    """DamageUnit armor list (DamageUnit::WriteArmorInfo 0x102102b0): u32 count | count x 32-byte ArmorInfo
    (i32 type (8 = skipped) | i32 max hp | f32 current hp | 4 x f32 | i32 -1). Robot::UpdateHUD shows
    entry 0 as cur/max, and DamageUnit::GetArmor(i) aborts if the list is empty. Also the body of
    GS_Object_Armor (19,50005) after the object id, which is how damage reaches the client."""
    cur = float(hp if cur is None else cur)
    armor = [(t, hp, cur, cur, 0.0, 0.0, 0.0, -1) for t in range(4)]
    return struct.pack("<I", len(armor)) + b"".join(struct.pack("<ii5fi", *a) for a in armor)


def robot(parts, pos=(0.0, 0.0, 0.0), rot=IDENTITY, char=0, power=None, hp=1000, nation=0):
    """Robot::WriteSnapshot (0x10250140) = WorldObject | Robot::WriteSystemPartSnapshot (0x1024dc90) | u32 skill fw."""
    return (world_object(pos=pos, rot=rot, char=char, b=nation, c=1)  # target type 1 = robot
            + system_parts(parts, pos=pos, power=power, hp=hp) + struct.pack("<i", -1))


def system_parts(parts, pos=(0.0, 0.0, 0.0), power=None, hp=1000, speed=8.0, jump=5.0):
    """Robot::WriteSystemPartSnapshot (0x1024dc90), also the body of GS_Robot_SystemPart (19,50104) after
    u32 robot id | u8 full (reads the function/skill/state fields below) | u8 avatar:
        u32 part count | count x (u32 fw | u32 object id | part snapshot)   -- index 0 body, 1 armor, 2 core,
            then one entry per equipment slot (armor PrimarySlotCount + SecondarySlotCount; u32 -1 = empty slot).
            Robot::AddSystemPart indexes parts[3 + slot] and aborts (CRT invalid parameter) if entries are missing.
        u32 function fw (-1 = none) [u32 object id | function snapshot, see func_power] | u32 skill fw (-1) | u32 skill fw (-1)
        u32 (+0xfc) | f32 speed (+0x174) | f32 jump (+0x168) | f32 (+0x178) | u32 pose (+0x18c) | 9 x u32 | vec3 (+0x30) | vec3 | i32 establishment id (+0x13c, -1 = none) | u32 (+0x140)
        | u32 n x u32 list | f32 (+0x1d8) | str (+0x76c) | DamageUnit armor info (armor_info)
    For a local robot it ends by raising SystemPartChange (9,40150), or EstabStateChange (9,40156) when +0x13c is
    not -1; the 40150 observer shows the status HUD if the robot is already registered.
    parts: list of (flyweight id, object id, snapshot bytes)
    power: optional (flyweight id, object id, snapshot bytes) for the energy function (robot +0xe4)
    """
    out = struct.pack("<I", len(parts))
    for fw, oid, snap in parts:
        if fw == -1:  # empty equipment slot: no object id or snapshot follows
            out += struct.pack("<i", -1)
        else:
            out += struct.pack("<II", fw, oid) + snap
    out += struct.pack("<II", power[0], power[1]) + power[2] if power else struct.pack("<i", -1)
    out += struct.pack("<ii", -1, -1)
    # u32 (+0xfc) | f32 move speed (+0x174, scaled by the body's Back/Strafe coefficients) | f32 jump speed
    # (+0x168, Robot::Jump) | f32 (+0x178) | u32 pose (+0x18c) | 9 x u32
    out += struct.pack("<I3f10I", 0, speed, jump, 0.0, *([0] * 10))
    out += struct.pack("<3f3fiI", *pos, 0.0, 0.0, 0.0, -1, 0)
    out += struct.pack("<I", 0)          # list count
    out += struct.pack("<f", 0.0) + b"\x00"  # f32, empty string (varint length 0)
    out += armor_info(hp)
    return out


def equip(robot_id=-1, estab_id=-1, ammo=0.0, max_ammo=None, **kw):
    """Equip::WriteSnapshot (0x10214db0): SystemPart | i32 owning robot id (-1) | i32 establishment id (-1)
    | u32 (+0x128) | i32 linked part (+0x12c, -1 = none) | u32 (+0x130) | f32 ammo (+0xe0) | f32 max ammo (+0x364) | f32 (+0x368)
    ammo: rounds/energy left. Weapon::CanBeUsed needs it >= the fire mode's cost (flyweight mode +0x9d8) when the
    flyweight has a magazine (+0x160); with 0 the weapon starts reloading instead of firing.
    max_ammo: Weapon::updateTimeCount clamps ammo to it and a reload (FastRecruit, key R) refills to it."""
    return system_part(**kw) + struct.pack("<iiIiIfff", robot_id, estab_id, 0, -1, 0, ammo, ammo if max_ammo is None else max_ammo, 0.0)


def weapon(robot_id=-1, mode=0, state=0, ammo=120.0, timing=(0.0,) * 6, **kw):
    """Weapon::WriteSnapshot (0x10277a60), instantiated by FWeapon: Equip | u32 fire mode (+0x370) | u32 state (+0x374)
    | 6 x f32 (+0x6e8..) | i32 skill fw (-1) [u32 id | skill] | i32 skill fw (-1) [u32 id | skill]
    +0x370 indexes the flyweight's fire-mode table (stride 0xb0, Weapon::UpdateHUD) - keep it small.
    timing: per fire mode (2 x 3 f32 at +0x6e8), copied from flyweight mode +0x9d4 (seconds between shots), +0xa34,
    +0xa38 by Weapon::OnCreate and then overwritten by this snapshot. Zero intervals fire a shot every frame.
    +0x374 is the weapon state: Weapon::CanBeTakeUp only allows 0 or 6, and UpdateInput sets 2 while firing."""
    return (equip(robot_id=robot_id, ammo=ammo, **kw) + struct.pack("<II", mode, state)
            + struct.pack("<6f", *timing) + struct.pack("<ii", -1, -1))
