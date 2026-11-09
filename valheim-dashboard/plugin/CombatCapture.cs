using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using HarmonyLib;
using UnityEngine;

public sealed partial class ValheimObserver
{
    private const string CombatAdapter = "server_owned_v1";
    private sealed class CombatTarget
    {
        internal float Seen;
        internal readonly HashSet<string> Participants = new HashSet<string>();
    }
    internal sealed class CombatHit
    {
        internal ZDO Zdo;
        internal string Subject, Creator, Skill;
        internal float Before, Level;
    }
    private readonly Dictionary<string,CombatTarget> combatTargets = new Dictionary<string,CombatTarget>();
    private readonly Dictionary<string,float> combatDeaths = new Dictionary<string,float>();
    private long combatCacheLosses;

    private string CombatCreator(HitData hit)
    {
        if(hit==null || hit.m_attacker==ZDOID.None)return null;
        var zdo=ZDOMan.instance.GetZDO(hit.m_attacker);
        long creator=zdo==null?0:zdo.GetLong("playerID",0L);
        return creator==0?null:creator.ToString(System.Globalization.CultureInfo.InvariantCulture);
    }
    private bool OwnedCreature(Character character,ZNetView view)
    {
        return Ready && character!=null && !character.IsPlayer() && view!=null
            && view.IsValid() && view.IsOwner() && view.GetZDO()!=null;
    }
    internal CombatHit BeforeCombatDamage(Character character,ZNetView view,HitData hit)
    {
        if(!OwnedCreature(character,view))return null;
        string creator=CombatCreator(hit);
        if(creator==null)return null;
        float health=character.GetHealth();
        if(health<=0 || character.IsDead())return null;
        return new CombatHit{Zdo=view.GetZDO(),Subject=view.GetZDO().m_uid.ToString(),
            Creator=creator,Before=health,Skill=hit.m_skill.ToString(),Level=hit.m_skillLevel};
    }
    internal void AfterCombatDamage(CombatHit hit)
    {
        if(hit==null || !Ready)return;
        float after=hit.Zdo.GetFloat("health",hit.Before);
        float damage=Mathf.Min(hit.Before,hit.Before-after);
        if(float.IsNaN(damage) || float.IsInfinity(damage) || damage<=0)return;
        if(!combatTargets.TryGetValue(hit.Subject,out var target))
        {
            if(combatTargets.Count<8192){target=new CombatTarget();combatTargets[hit.Subject]=target;}
            else combatCacheLosses++;
        }
        if(target!=null)
        {
            target.Seen=Time.realtimeSinceStartup;
            if(target.Participants.Count<64)target.Participants.Add(hit.Creator);
        }
        Emit("combat_hit",D("source",CombatAdapter,"subject",hit.Subject,"creator",hit.Creator,
            "damage",damage,"skill",hit.Skill,"skill_level",hit.Level));
    }
    internal void CombatDeath(Character character,ZNetView view,HitData lastHit)
    {
        // OnDeath is also used by scripted removals: only a dead, server-owned
        // creature qualifies. A destroyed/unloaded ZDO alone never means a kill.
        if(!OwnedCreature(character,view) || character.GetHealth()>0)return;
        var zdo=view.GetZDO();string subject=zdo.m_uid.ToString();
        if(combatDeaths.ContainsKey(subject))return;
        if(combatDeaths.Count>=8192){combatCacheLosses++;return;}
        combatDeaths[subject]=Time.realtimeSinceStartup;
        string killer=CombatCreator(lastHit);
        var participants=combatTargets.TryGetValue(subject,out var target)
            ?new HashSet<string>(target.Participants):new HashSet<string>();
        if(killer!=null)participants.Add(killer);
        string prefab="prefab_"+zdo.GetPrefab(),enemy=Label(character.m_name,prefab);
        if(catalog.TryGetValue(zdo.GetPrefab(),out var info)){prefab=info.Name;enemy=info.Label;}
        Emit("combat_death",D("source",CombatAdapter,"subject",subject,"prefab",prefab,
            "enemy",enemy,"killer",killer,"participants",participants.ToArray(),
            "tamed",character.IsTamed(),"boss",character.IsBoss()));
        combatTargets.Remove(subject);
    }
    private void PruneCombat(float now)
    {
        foreach(var key in combatTargets.Where(p=>now-p.Value.Seen>600).Select(p=>p.Key).ToArray())
        {combatTargets.Remove(key);combatCacheLosses++;}
        foreach(var key in combatDeaths.Where(p=>now-p.Value>600).Select(p=>p.Key).ToArray())combatDeaths.Remove(key);
    }
}

[HarmonyPatch(typeof(Character),"ApplyDamage")]
internal static class ValheimCombatDamagePatch
{
    private static void Prefix(Character __instance,ZNetView ___m_nview,HitData hit,out ValheimObserver.CombatHit __state)
    {
        __state=null;var o=ValheimObserver.Instance;if(o==null || !o.Ready)return;
        long start=Stopwatch.GetTimestamp();
        try{__state=o.BeforeCombatDamage(__instance,___m_nview,hit);}
        catch(Exception e){o.Fault(e);}finally{o.Timing(start);}
    }
    private static void Postfix(ValheimObserver.CombatHit __state)
    {
        var o=ValheimObserver.Instance;if(o==null || __state==null)return;
        long start=Stopwatch.GetTimestamp();
        try{o.AfterCombatDamage(__state);}catch(Exception e){o.Fault(e);}finally{o.Timing(start);}
    }
}

[HarmonyPatch(typeof(Character),"OnDeath")]
internal static class ValheimCombatDeathPatch
{
    private static void Prefix(Character __instance,ZNetView ___m_nview,HitData ___m_lastHit)
    {
        var o=ValheimObserver.Instance;if(o==null || !o.Ready)return;
        long start=Stopwatch.GetTimestamp();
        try{o.CombatDeath(__instance,___m_nview,___m_lastHit);}
        catch(Exception e){o.Fault(e);}finally{o.Timing(start);}
    }
}
