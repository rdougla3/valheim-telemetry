using System;
using System.Collections.Generic;
using System.Collections.Concurrent;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Threading;
using BepInEx;
using HarmonyLib;
using Newtonsoft.Json;
using UnityEngine;

[BepInPlugin("local.valheim.observer", "Valheim Observer", "2.2.0")]
public sealed partial class ValheimObserver : BaseUnityPlugin
{
    internal static ValheimObserver Instance;
    internal bool Ready;
    private string output, directory, boot = Guid.NewGuid().ToString("N"), world;
    private readonly ConcurrentQueue<Dictionary<string,object>> queue = new ConcurrentQueue<Dictionary<string,object>>();
    private readonly AutoResetEvent wake = new AutoResetEvent(false);
    private Thread writer;
    private Harmony harmony;
    private bool adaptersEnabled;
    private volatile bool stopping, storageFailed;
    private long seq, dropped, queued, hookTicks, hookCalls, maxTicks, failures;
    private float nextSample, nextHeartbeat, nextHealth;
    private readonly Dictionary<long,string> known = new Dictionary<long,string>();
    private readonly Dictionary<long,string> creators = new Dictionary<long,string>();
    private double clock;
    internal static Dictionary<string,object> D(params object[] args)
    {
        var d = new Dictionary<string,object>();for(int i=0;i<args.Length;i+=2)d[(string)args[i]]=args[i+1];return d;
    }
    private void Awake()
    {
        Instance=this;
        output=Environment.GetEnvironmentVariable("VALHEIM_LIVE_PATH") ?? Path.Combine(Paths.GameRootPath,"dashboard-data","live.json");
        directory=Path.Combine(Path.GetDirectoryName(output),"telemetry");
        writer=new Thread(WriteLoop){IsBackground=true,Name="Valheim telemetry writer"};writer.Start();
        harmony=new Harmony("local.valheim.observer.telemetry");
        if(Version.CurrentVersion.ToString()=="1.0.20")
        {
            try{harmony.PatchAll(typeof(ValheimObserver).Assembly);adaptersEnabled=true;}
            catch(Exception e){harmony.UnpatchSelf();Logger.LogWarning("Telemetry patches disabled: "+e.Message);}
        }
        else Logger.LogWarning("Telemetry adapters disabled for unverified Valheim version; identity/clock remain available.");
        Logger.LogInfo("Valheim Observer 2.2 ready; passive movement/farming capture and confirmed server-owned combat.");
    }
    private void Update()
    {
        if(ZNet.instance==null || !ZNet.instance.IsServer() || !ZNet.instance.IsDedicated() || ZDOMan.instance==null || ZNetScene.instance==null || ZNet.World==null)return;
        var start=Stopwatch.GetTimestamp();
        try
        {
            clock=ZNet.instance.GetTimeSeconds();
            if(!Ready)
            {
                world=ZNet.World.m_uid.ToString(CultureInfo.InvariantCulture);BuildCatalog();Ready=true;
                ZDOMan.instance.m_onZDODestroyed+=Destroyed;
                Emit("boot",D("version","2.2.0","game",Version.GetVersionString(),"modules",new[]{"identity","clock","movement","sailing","planting","harvest","stations","combat"},"adapter_enabled",adaptersEnabled,"combat_adapter",adaptersEnabled?CombatAdapter:"disabled"));
            }
            float now=Time.realtimeSinceStartup;
            if(now>=nextSample){nextSample=now+1;SamplePlayers();}
            if(now>=nextHeartbeat){nextHeartbeat=now+5;Heartbeat();}
            if(now>=nextHealth)
            {
                nextHealth=now+60;
                Emit("health",D("dropped",Interlocked.Read(ref dropped),"queued",Interlocked.Read(ref queued),"observer_calls",hookCalls,"observer_ms",hookTicks*1000.0/Stopwatch.Frequency,"max_call_ms",maxTicks*1000.0/Stopwatch.Frequency,"errors",failures,"storage_failed",storageFailed,"state_entries",states.Count,"combat_targets",combatTargets.Count,"combat_cache_losses",combatCacheLosses));
                hookTicks=hookCalls=maxTicks=0;Prune(now);
            }
        }
        catch(Exception e){Fault(e);}
        finally{Timing(start);}
    }
    internal void Timing(long start){long ticks=Stopwatch.GetTimestamp()-start;hookTicks+=ticks;hookCalls++;if(ticks>maxTicks)maxTicks=ticks;}
    internal void Fault(Exception e){if(++failures<=5 || failures%100==0)Logger.LogWarning("Telemetry observation failed: "+e.GetType().Name+": "+e.Message);}
    internal void Emit(string kind,Dictionary<string,object> data)
    {
        long id=Interlocked.Increment(ref seq);
        // Conservative 2 KiB per record; core records can use the reserved final quarter.
        bool core=kind=="boot" || kind=="health" || kind=="identity";
        if((storageFailed && !core) || Interlocked.Read(ref queued)>=(core?4096:3072)){Interlocked.Increment(ref dropped);return;}
        var e=D("schema",1,"boot",boot,"seq",id,"world",world,"ts",DateTimeOffset.UtcNow.ToUnixTimeMilliseconds()/1000.0,"clock",clock,"kind",kind,"data",data);
        queue.Enqueue(e);Interlocked.Increment(ref queued);wake.Set();
    }
    private void EnqueueControl(string key,object value)
    {
        if(Interlocked.Read(ref queued)>=4096){Interlocked.Increment(ref dropped);return;}
        queue.Enqueue(D(key,value));Interlocked.Increment(ref queued);wake.Set();
    }
    private void Heartbeat()
    {
        var rows=new List<object>();var present=new HashSet<long>();
        foreach(var peer in ZNet.instance.GetPeers())
        {
            if(peer==null || !peer.IsReady())continue;
            present.Add(peer.m_uid);var zdo=ZDOMan.instance.GetZDO(peer.m_characterID);
            long c=zdo==null?0:zdo.GetLong("playerID",0L);string creator=c==0?null:c.ToString(CultureInfo.InvariantCulture);
            if(creator!=null)creators[peer.m_uid]=creator;
            if(creator==null)creators.TryGetValue(peer.m_uid,out creator);
            string host=peer.m_rpc.GetSocket().GetHostName();
            var row=D("peer",peer.m_uid.ToString(CultureInfo.InvariantCulture),"name",peer.m_playerName,"platform_id",host,"creator_id",creator);
            rows.Add(row);string json=JsonConvert.SerializeObject(row);
            if(creator!=null && (!known.TryGetValue(peer.m_uid,out var old) || old!=json))
            {known[peer.m_uid]=json;Logger.LogInfo("VALHEIM_IDENTITY "+json);Emit("identity",row);}
        }
        foreach(long id in new List<long>(known.Keys))if(!present.Contains(id)){known.Remove(id);creators.Remove(id);}
        var heartbeat=D("observed_at",DateTime.UtcNow.ToString("o"),"world_seconds",clock,"day",EnvMan.instance==null?-1:EnvMan.instance.GetDay(),"day_length",EnvMan.instance==null?1800:EnvMan.instance.m_dayLengthSec,"players",rows,"telemetry_boot",boot,"telemetry_dropped",Interlocked.Read(ref dropped));
        EnqueueControl("heartbeat",heartbeat);
    }
    private void SamplePlayers()
    {
        foreach(var peer in ZNet.instance.GetPeers())
        {
            if(peer==null || !peer.IsReady())continue;
            var zdo=ZDOMan.instance.GetZDO(peer.m_characterID);if(zdo==null)continue;
            long c=zdo.GetLong("playerID",0L);if(c==0)continue;
            string creator=c.ToString(CultureInfo.InvariantCulture);creators[peer.m_uid]=creator;
            Vector3 pos=zdo.GetPosition();var parentID=zdo.GetConnectionZDOID(ZDOExtraData.ConnectionType.SyncTransform);var parent=ZDOMan.instance.GetZDO(parentID);
            bool ship=parent!=null && catalog.TryGetValue(parent.GetPrefab(),out var info) && info.Type=="ship";
            Vector3 shipPos=ship?parent.GetPosition():Vector3.zero;
            if(parent!=null){Vector3 rel=zdo.GetVec3("relPos",Vector3.zero);pos=parent.GetPosition()+parent.GetRotation()*rel;}
            bool stale=positionFresh.TryGetValue(zdo.m_uid.ToString(),out float last) && Time.realtimeSinceStartup-last>3;
            Emit("sample",D("creator",creator,"life",zdo.m_uid.ToString(),"x",pos.x,"z",pos.z,"mono",(double)Time.realtimeSinceStartup,
                "dead",zdo.GetBool("dead",false),"stale",stale,"ship",ship?parentID.ToString():null,"ship_x",shipPos.x,"ship_z",shipPos.z,
                "helm",ship && parent.GetLong("user",0L)==c,"mounted",parent!=null && !ship));
        }
    }
    private void WriteLoop()
    {
        FileStream stream=null;StreamWriter lines=null;int part=0;DateTime opened=DateTime.MinValue,lastSync=DateTime.MinValue,lastCheck=DateTime.MinValue;
        try
        {
            Directory.CreateDirectory(directory);
            while(!stopping || !queue.IsEmpty)
            {
                if(queue.TryDequeue(out var e))
                {
                    Interlocked.Decrement(ref queued);
                    try
                    {
                        if(e.TryGetValue("heartbeat",out var hb)){AtomicJson(output,hb);continue;}
                        if(e.TryGetValue("catalog",out var cat)){AtomicJson(Path.Combine(Path.GetDirectoryName(output),"prefabs.json"),cat);continue;}
                        if(DateTime.UtcNow-lastCheck>TimeSpan.FromMinutes(1))
                        {
                            lastCheck=DateTime.UtcNow;long bytes=0;foreach(var f in Directory.EnumerateFiles(directory,"*.jsonl"))bytes+=new FileInfo(f).Length;
                            storageFailed=bytes>1024L*1024*1024;
                        }
                        if(storageFailed){Interlocked.Increment(ref dropped);continue;}
                        if(stream==null || stream.Length>16*1024*1024 || DateTime.UtcNow-opened>TimeSpan.FromMinutes(5))
                        {
                            lines?.Flush();stream?.Flush(true);lines?.Dispose();stream?.Dispose();
                            string file=Path.Combine(directory,DateTime.UtcNow.ToString("yyyyMMddTHHmmss")+"-"+boot+"-"+(part++).ToString("D5")+".jsonl");
                            stream=new FileStream(file,FileMode.CreateNew,FileAccess.Write,FileShare.Read);lines=new StreamWriter(stream,new System.Text.UTF8Encoding(false),65536,true);opened=DateTime.UtcNow;
                        }
                        lines.WriteLine(JsonConvert.SerializeObject(e));
                    }
                    catch(Exception)
                    {storageFailed=true;Interlocked.Increment(ref dropped);try{lines?.Dispose();stream?.Dispose();}catch{}lines=null;stream=null;}
                }
                else{lines?.Flush();wake.WaitOne(1000);}
                if(stream!=null && DateTime.UtcNow-lastSync>TimeSpan.FromSeconds(5)){lines.Flush();stream.Flush(true);lastSync=DateTime.UtcNow;}
            }
        }
        catch(Exception){storageFailed=true;}
        finally{try{lines?.Flush();stream?.Flush(true);lines?.Dispose();stream?.Dispose();}catch{}}
    }
    private static void AtomicJson(string path,object data)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(path));File.WriteAllText(path+".tmp",JsonConvert.SerializeObject(data),new System.Text.UTF8Encoding(false));
        if(File.Exists(path))File.Replace(path+".tmp",path,null);else File.Move(path+".tmp",path);
    }
    private void OnDestroy()
    {
        Ready=false;if(ZDOMan.instance!=null)ZDOMan.instance.m_onZDODestroyed-=Destroyed;
        harmony?.UnpatchSelf();stopping=true;wake.Set();writer?.Join(1500);if(Instance==this)Instance=null;
    }
}
