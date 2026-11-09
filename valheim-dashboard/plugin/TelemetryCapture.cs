using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using HarmonyLib;
using UnityEngine;

public sealed partial class ValheimObserver
{
    private sealed class PrefabInfo
    {
        public string Name,Type,Label,Category;public int Slots;
    }
    private sealed class ObjectState
    {
        public float Seen;public string Signature;public bool Picked;public int Cycle;
    }
    private readonly Dictionary<int,PrefabInfo> catalog=new Dictionary<int,PrefabInfo>();
    private readonly Dictionary<string,ObjectState> states=new Dictionary<string,ObjectState>();
    private readonly Dictionary<string,float> newObjects=new Dictionary<string,float>();
    private readonly Dictionary<string,float> positionFresh=new Dictionary<string,float>();
    private readonly Dictionary<string,float> messages=new Dictionary<string,float>();
    private readonly Dictionary<int,string> methods=new Dictionary<int,string>();
    private static string Label(string token,string fallback)
    {
        if(string.IsNullOrEmpty(token))return fallback;
        try{string s=Localization.instance.Localize(token);return s.StartsWith("$")?fallback:s;}catch{return fallback;}
    }
    private void BuildCatalog()
    {
        var excluded=new HashSet<string>();
        foreach(var prefab in ZNetScene.instance.m_prefabs)
        {
            if(prefab==null)continue;
            var info=new PrefabInfo{Name=prefab.name,Label=prefab.name,Type="other",Category="wild"};
            var plant=prefab.GetComponent<Plant>();var pick=prefab.GetComponent<Pickable>();
            if(plant!=null)
            {
                info.Type="plant";info.Label=Label(plant.m_name,prefab.name);info.Category=prefab.name.ToLowerInvariant().Contains("vine")?"vine":"crop";
                foreach(var grown in plant.m_grownPrefabs)if(grown!=null)
                {excluded.Add(grown.name);if(grown.GetComponent<TreeBase>()!=null)info.Category="tree";}
                if(prefab.name.ToLowerInvariant().Contains("seed"))info.Category="seed crop";
                excluded.Add(prefab.name);
            }
            else if(pick!=null)
            {info.Type="pickable";info.Label=pick.m_itemPrefab==null?prefab.name:Label(pick.m_itemPrefab.GetComponent<ItemDrop>()?.m_itemData.m_shared.m_name,prefab.name);info.Category=pick.m_harvestStat==PlayerStatType.HarvestCrop?"crop":pick.m_harvestStat==PlayerStatType.HarvestBerry?"berry":pick.m_harvestStat==PlayerStatType.HarvestMushroom?"mushroom":pick.m_harvestStat==PlayerStatType.HarvestVine?"vine":"gathering";}
            else if(prefab.GetComponent<Ship>()!=null)info.Type="ship";
            else if(prefab.GetComponent<Character>() is Character character){info.Type=character is Player?"player":"mob";info.Label=Label(character.m_name,prefab.name);}
            else if(prefab.GetComponent<CookingStation>() is CookingStation cook){info.Type="cooking";info.Slots=Math.Min(32,cook.m_slots.Length);}
            else if(prefab.GetComponent<Smelter>()!=null)info.Type="smelter";
            else if(prefab.GetComponent<Fermenter>()!=null)info.Type="fermenter";
            if(info.Type!="other")catalog[prefab.name.GetStableHashCode()]=info;
        }
        foreach(string name in new[]{"RPC_Pick","Pick","RPC_SetPicked","RPC_AddItem","RPC_AddOre","RPC_AddFuel","RPC_RemoveDoneItem","RPC_Tap"})methods[name.GetStableHashCode()]=name;
        EnqueueControl("catalog",D("game",Version.GetVersionString(),"exclude_building_prefabs",excluded.ToArray(),"prefabs",catalog.Values.Select(i=>(object)D("name",i.Name,"type",i.Type,"label",i.Label,"category",i.Category)).ToArray()));
        Logger.LogInfo("Telemetry classified "+catalog.Count+" prefabs; "+excluded.Count+" plant/grown prefabs excluded from building counts.");
    }
    private void Prune(float now)
    {
        PruneCombat(now);
        foreach(var key in newObjects.Where(p=>now-p.Value>60).Select(p=>p.Key).ToArray())newObjects.Remove(key);
        foreach(var key in states.Where(p=>now-p.Value.Seen>600).Select(p=>p.Key).ToArray())states.Remove(key);
        foreach(var key in messages.Where(p=>now-p.Value>60).Select(p=>p.Key).ToArray())messages.Remove(key);
        foreach(var key in positionFresh.Where(p=>now-p.Value>600).Select(p=>p.Key).ToArray())positionFresh.Remove(key);
    }
    internal void Created(ZDO zdo)
    {if(Ready && newObjects.Count<4096)newObjects[zdo.m_uid.ToString()]=Time.realtimeSinceStartup;}
    internal void Observed(ZDO zdo)
    {
        if(!Ready)return;
        if(!catalog.TryGetValue(zdo.GetPrefab(),out var info)){newObjects.Remove(zdo.m_uid.ToString());return;}
        string id=zdo.m_uid.ToString();float now=Time.realtimeSinceStartup;
        if(info.Type!="plant")newObjects.Remove(id);
        if(info.Type=="player"){if(positionFresh.Count<256 || positionFresh.ContainsKey(id))positionFresh[id]=now;return;}
        if(info.Type=="plant")
        {
            if(newObjects.ContainsKey(id))
            {
                long creator=zdo.GetLong("creator",0L),planted=zdo.GetLong("plantTime",0L);
                if(creator!=0 && planted!=0){Emit("plant",D("subject",id,"creator",creator.ToString(),"planted_at",planted.ToString(),"plant",info.Label,"category",info.Category));newObjects.Remove(id);}
            }
            return;
        }
        if(info.Type!="pickable" && info.Type!="cooking" && info.Type!="smelter" && info.Type!="fermenter")return;
        if(!states.TryGetValue(id,out var state))
        {if(states.Count>=8192){dropped++;return;}state=new ObjectState();states[id]=state;}
        state.Seen=now;
        if(info.Type=="pickable")
        {
            bool picked=zdo.GetBool("picked",false);
            if(state.Signature!=null && !state.Picked && picked)Harvest(zdo,info,state);
            if(state.Picked && !picked)state.Cycle++;
            state.Picked=picked;state.Signature="pickable";return;
        }
        Dictionary<string,object> data=D("subject",id,"type",info.Type,"station",info.Name);string signature="";
        if(info.Type=="cooking")
        {
            var slots=new List<object>();for(int i=0;i<info.Slots;i++)
            {string item=zdo.GetString("slot"+i,"");int status=zdo.GetInt("slotstatus"+i,0);slots.Add(D("item",item,"status",status));signature+=item+":"+status+";";}data["slots"]=slots;
        }
        else if(info.Type=="smelter"){int n=zdo.GetInt("queued",0);data["queue"]=n;data["item"]=zdo.GetString("item0","");signature=n+":"+data["item"];}
        else{int content=zdo.GetInt("Content",0);data["content"]=content;data["started"]=zdo.GetLong("StartTime",0L).ToString();signature=content+":"+data["started"];}
        if(signature!=state.Signature){state.Signature=signature;Emit("station",data);}
    }
    private void Harvest(ZDO zdo,PrefabInfo info,ObjectState state)
    {Emit("harvest",D("subject",zdo.m_uid.ToString(),"plant",info.Label,"category",info.Category,"cycle",state.Cycle));}
    private void Destroyed(ZDO zdo)
    {
        if(!Ready)return;
        try
        {
            string id=zdo.m_uid.ToString();
            combatTargets.Remove(id);
            states.Remove(id);newObjects.Remove(id);positionFresh.Remove(id);
        }
        catch(Exception e){Fault(e);}
    }
    private string Creator(long peer){creators.TryGetValue(peer,out var creator);return creator;}
    internal void Routed(ZRpc rpc,ZPackage original)
    {
        if(!Ready || original.Size()>16384){return;}
        var pkg=new ZPackage(original.GetArray());var r=new ZRoutedRpc.RoutedRPCData();r.Deserialize(pkg);
        if(!methods.TryGetValue(r.m_methodHash,out string method))return;
        var source=ZNet.instance.GetPeers().Find(p=>p.m_rpc==rpc);
        if(source==null || source.m_uid!=r.m_senderPeerID)return;
        string messageKey=r.m_senderPeerID+":"+r.m_msgID;
        if(messages.ContainsKey(messageKey))return;
        if(messages.Count>=8192){dropped++;return;}messages[messageKey]=Time.realtimeSinceStartup;
        ObserveRpc(r.m_senderPeerID,r.m_targetPeerID,r.m_targetZDO,method,r.m_parameters);
    }
    internal bool Supports(string method){return methods.ContainsKey(method.GetStableHashCode());}
    internal void ObserveRpc(long sender,long target,ZDOID subject,string method,ZPackage p)
    {
        var zdo=ZDOMan.instance.GetZDO(subject);if(zdo==null || !catalog.TryGetValue(zdo.GetPrefab(),out var info))return;
        if(info.Type=="pickable")
        {
            string id=subject.ToString();
            if(method=="RPC_Pick" || method=="Pick")Emit("request",D("subject",id,"creator",Creator(sender),"action","pick"));
            else if(method=="RPC_SetPicked")
            {
                bool picked=p.ReadBool();
                if(!states.TryGetValue(id,out var st)){if(states.Count>=8192)return;st=new ObjectState();states[id]=st;}
                if(picked && !st.Picked)Harvest(zdo,info,st);if(!picked && st.Picked)st.Cycle++;
                st.Picked=picked;st.Signature="pickable";st.Seen=Time.realtimeSinceStartup;
            }
            return;
        }
        if(info.Type=="cooking" || info.Type=="smelter" || info.Type=="fermenter")
        {
            string item=null;
            if(method=="RPC_AddItem" && info.Type=="cooking")item=p.ReadString();
            if(method=="RPC_AddOre")item=p.ReadString();
            if(method=="RPC_AddItem" || method=="RPC_AddOre" || method=="RPC_AddFuel" || method=="RPC_RemoveDoneItem" || method=="RPC_Tap")
                Emit("request",D("subject",subject.ToString(),"creator",Creator(sender),"action",method=="RPC_AddItem" || method=="RPC_AddOre"?"add":method=="RPC_AddFuel"?"fuel":"remove","item",item));
        }
    }
}

[HarmonyPatch(typeof(ZRoutedRpc),"InvokeRoutedRPC",new Type[]{typeof(long),typeof(ZDOID),typeof(string),typeof(object[])})]
internal static class ValheimLocalRpcPatch
{
    private static void Prefix(long targetPeerID,ZDOID targetZDO,string methodName,object[] parameters)
    {
        var o=ValheimObserver.Instance;if(o==null || !o.Ready || !o.Supports(methodName))return;long t=Stopwatch.GetTimestamp();
        try{var p=new ZPackage();ZRpc.Serialize(parameters,ref p);if(p.Size()<=16384){p.SetPos(0);o.ObserveRpc(ZDOMan.GetSessionID(),targetPeerID,targetZDO,methodName,p);}}
        catch(Exception e){o.Fault(e);}finally{o.Timing(t);}
    }
}

[HarmonyPatch(typeof(ZDO),"Deserialize")]
internal static class ValheimZdoPatch
{
    private static void Postfix(ZDO __instance)
    {var o=ValheimObserver.Instance;if(o==null || !o.Ready)return;long t=Stopwatch.GetTimestamp();try{o.Observed(__instance);}catch(Exception e){o.Fault(e);}finally{o.Timing(t);}}
}
[HarmonyPatch(typeof(ZDOMan),"CreateNewZDO",new Type[]{typeof(ZDOID),typeof(Vector3),typeof(int)})]
internal static class ValheimCreatePatch
{
    private static void Postfix(ZDO __result)
    {var o=ValheimObserver.Instance;if(o==null || !o.Ready)return;try{o.Created(__result);}catch(Exception e){o.Fault(e);}}
}
[HarmonyPatch(typeof(ZRoutedRpc),"RPC_RoutedRPC")]
internal static class ValheimRpcPatch
{
    private static void Prefix(ZRpc rpc,ZPackage pkg)
    {var o=ValheimObserver.Instance;if(o==null || !o.Ready)return;long t=Stopwatch.GetTimestamp();try{o.Routed(rpc,pkg);}catch(Exception e){o.Fault(e);}finally{o.Timing(t);}}
}
