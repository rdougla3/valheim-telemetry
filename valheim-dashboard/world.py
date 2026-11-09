"""Read-only Valheim v40–42 parser. See THIRD_PARTY.md for format references."""
from pathlib import Path
from collections import Counter, defaultdict
import gzip
import hashlib
import json
import math
import struct
import config

# Cultivator plant prefabs from Jotunn's generated vanilla piece registry. The runtime
# observer manifest extends this with installed prefabs and their grown replacements.
PLANT_PREFABS = {
    'sapling_turnip','sapling_seedturnip','sapling_onion','sapling_seedonion',
    'sapling_carrot','sapling_seedcarrot','sapling_barley','sapling_flax',
    'sapling_jotunpuffs','sapling_magecap','sapling_oat','sapling_poteitr',
    'sapling_Kale','sapling_seedkale','Beech_Sapling','Birch_Sapling','Oak_Sapling',
    'FirTree_Sapling','PineTree_Sapling','FirTree_big_Sapling','VineAsh_sapling','VineGreen_sapling'}


def plant_filter():
    names=set(PLANT_PREFABS)
    try:
        manifest=json.loads((config.DATA_DIR/'prefabs.json').read_text())
        names.update(manifest['exclude_building_prefabs'])
    except (OSError, ValueError, KeyError, TypeError): pass
    signature=hashlib.sha256('\n'.join(sorted(names)).encode()).hexdigest()[:16]
    return {stable_hash(name) for name in names},signature


def stable_hash(s):
    a = b = 5381
    for i, c in enumerate(s):
        if i % 2:
            b = ((b * 33) ^ ord(c)) & 0xffffffff
        else:
            a = ((a * 33) ^ ord(c)) & 0xffffffff
    return (a + b * 1566083941) & 0xffffffff


class Reader:
    def __init__(self, data): self.data, self.i = data, 0
    def get(self, fmt):
        values = struct.unpack_from('<' + fmt, self.data, self.i)
        self.i += struct.calcsize('<' + fmt)
        return values[0] if len(values) == 1 else values
    def take(self, n):
        if n < 0 or self.i + n > len(self.data): raise ValueError('Truncated save field')
        b = self.data[self.i:self.i+n]; self.i += n
        return b
    def skip(self, n): self.take(n)
    def num(self):
        n = self.get('B')
        return ((n & 127) << 8) | self.get('B') if n & 128 else n
    def string(self):
        n = shift = 0
        for _ in range(5):
            b = self.get('B'); n |= (b & 127) << shift
            if b < 128: return self.take(n).decode('utf-8')
            shift += 7
        raise ValueError('Invalid string length')
    def end(self):
        if self.i != len(self.data): raise ValueError(f'Unexpected trailing bytes: {len(self.data)-self.i}')


def generation(path):
    return max((int(f.name.split('.')[1]) for f in path.glob('_main.*.ok')
                if all((path / f'_main.{f.name.split(".")[1]}.{ext}').is_file()
                       for ext in ('db2', 'fwl2', 'chunks'))), default=None)


def cluster_builds(pieces):
    cells = defaultdict(list)
    for piece in pieces:
        x, z = piece['x'], piece['z']
        cells[(math.floor(x/32), math.floor(z/32))].append(piece)
    clusters = []
    remaining = set(cells)
    while remaining:
        start = remaining.pop(); stack = [start]; group = []
        while stack:
            cell = stack.pop(); group.extend(cells[cell])
            for dx in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    neighbor = (cell[0]+dx, cell[1]+dz)
                    if neighbor in remaining:
                        remaining.remove(neighbor); stack.append(neighbor)
        if len(group) >= 5:
            x = sum(p['x'] for p in group)/len(group); z = sum(p['z'] for p in group)/len(group)
            clusters.append({'x': round(x,1), 'z': round(z,1), 'pieces': len(group),
                             'distance': round(math.hypot(x,z))})
    return sorted(clusters, key=lambda c: c['distance'], reverse=True)


BOSSES = [('Eikthyr','Meadows','defeated_eikthyr'), ('The Elder','Black Forest','defeated_gdking'),
          ('Bonemass','Swamp','defeated_bonemass'), ('Moder','Mountains','defeated_dragon'),
          ('Yagluth','Plains','defeated_goblinking'), ('The Queen','Mistlands','defeated_queen'),
          ('Fader','Ashlands','defeated_fader'), ('Kall Fimbulbringer','Deep North','defeated_frozenking_p3')]


def read_world(path):
    path = Path(path)
    # Capture a single committed generation in memory. Recheck it after reading,
    # so a concurrent autosave cannot produce a mixed snapshot.
    gen = generation(path)
    if gen is None: raise ValueError('No committed world save found')
    index = (path / f'_main.{gen}.chunks').read_bytes()
    header = (path / f'_main.{gen}.db2').read_bytes()
    metadata = (path / f'_main.{gen}.fwl2').read_bytes()
    saved_at = (path / f'_main.{gen}.ok').stat().st_mtime
    r = Reader(index); version, total, count = r.get('Hii')
    if not 40 <= version <= 42 or not 0 <= count <= 100000: raise ValueError('Unsupported chunk index')
    files = []
    for _ in range(count):
        ch, size, v, n = r.get('HBIi')
        name = f'{ch>>8:02x}_{ch&255:02x}__{size}_{v}.chunk'
        files.append((ch == 1 and size == 0, n, (path / name).read_bytes()))
    r.end()
    if generation(path) != gen: raise ValueError('World saved during read; retrying next cycle')
    r = Reader(header); version, net_time, compressed_len = r.get('idi')
    if not 40 <= version <= 42: raise ValueError('Unsupported world version')
    zone = Reader(gzip.decompress(r.take(compressed_len)))
    zone_count = zone.get('i')
    if not 0 <= zone_count <= 10000000: raise ValueError('Invalid zone count')
    zones = [zone.get('hh') for _ in range(zone_count)]
    location_version, key_count = zone.get('ii')
    if not 0 <= key_count <= 100000: raise ValueError('Invalid key count')
    keys = [zone.string() for _ in range(key_count)]
    zone.get('B'); locations = zone.get('i'); placed = 0
    for _ in range(locations):
        zone.skip(16); placed += bool(zone.get('B'))
    zone.end()
    f = Reader(metadata)
    f = Reader(f.take(f.get('i')))
    f.get('i'); f.string(); f.string(); f.skip(4+8+4+1)
    for _ in range(f.get('i')): f.string()
    history = []
    for _ in range(f.get('i')):
        account, display, assigned, playfab = f.string(), f.string(), f.string(), f.string()
        history.append({'platform_id':account, 'name':assigned or display})
    f.end()
    off_map_zones = sum(1 for x,z in zones if math.hypot(x*64,z*64)>10500)
    zones = [(x,z) for x,z in zones if math.hypot(x*64,z*64)<=10500]
    zone_count = len(zones)
    creator_key, tag_key = stable_hash('creator'), stable_hash('tag')
    creator_index_key = stable_hash('creatorIndex')
    associations = defaultdict(set)
    excluded_plants,filter_signature=plant_filter()
    builders = Counter(); pieces = []; portals = []; parsed = 0; plant_pieces = 0
    portal_hashes = {stable_hash(s) for s in ('portal_wood','portal','portal_stone')}
    for portal_chunk, expected, raw in files:
        r = Reader(raw); v, n = r.get('hi')
        if not 40 <= v <= 42 or n != expected: raise ValueError('Chunk header mismatch')
        for _ in range(n):
            flags = r.get('H')
            if flags & 0x2000:
                x, z = r.get('hh'); y = 0
            else: x, y, z = r.get('fff')
            prefab = r.get('I')
            if flags & 0x1000:
                rotation = r.get('H')
                if not rotation & 0x8000: r.skip(2)
            if flags & 1: r.skip(5)
            for flag, size in ((2,8),(4,16),(8,20)):
                if flags & flag: r.skip(r.num()*size)
            creator_index = None
            if flags & 16:
                for _ in range(r.num()):
                    k, value = r.get('Ii')
                    if k == creator_index_key: creator_index = value
            creator = None; tag = ''
            if flags & 32:
                for _ in range(r.num()):
                    k, value = r.get('Iq')
                    if k == creator_key and value: creator = str(value)
            if flags & 64:
                for _ in range(r.num()):
                    k = r.get('I'); value = r.string()
                    if k == tag_key: tag = value
            if flags & 128:
                for _ in range(r.num()): r.skip(4); r.skip(r.get('i'))
            if creator:
                if creator_index is not None and 0 <= creator_index < len(history): associations[creator].add(creator_index)
                if prefab in excluded_plants: plant_pieces += 1
                else:
                    builders[creator] += 1
                    pieces.append({'x':x, 'z':z, 'creator':creator})
            if portal_chunk or prefab in portal_hashes:
                portals.append({'x':round(x,1), 'z':round(z,1), 'tag':tag})
            parsed += 1
        r.end()
    if parsed != total: raise ValueError(f'Object count mismatch: {parsed} vs {total}')
    groups = defaultdict(list)
    for portal in portals: groups[portal['tag']].append(portal)
    pairs = [{'tag':tag, 'a':ps[0], 'b':ps[1]} for tag,ps in groups.items() if len(ps)==2]
    clusters = cluster_builds(pieces)
    return {'parser_version':4, 'plant_filter':filter_signature,'excluded_plants':plant_pieces, 'off_map_zones':off_map_zones, 'generation':gen, 'saved_at':saved_at, 'world_version':version,
            'world_seconds':net_time, 'day':int(net_time//1800), 'generated_zones':zone_count,
            'generated_km2':zone_count*64*64/1e6, 'placed_locations':placed,
            'objects':parsed, 'building_pieces':sum(builders.values()),
            'leaderboard':[{'creator_id':c,'pieces':n} for c,n in builders.most_common()],
            'identities':[{'creator_id':c, **history[next(iter(indices))]} for c,indices in associations.items() if len(indices)==1],
            'bosses':[{'name':name,'biome':biome,'defeated':key in keys} for name,biome,key in BOSSES],
            'global_keys':keys, 'portals':portals, 'portal_pairs':pairs,
            'unpaired_portals':sum(len(ps) for tag,ps in groups.items() if len(ps)!=2),
            'outposts':clusters, 'farthest_outpost':clusters[0] if clusters else None,
            'map':{'zones':zones,'buildings':[[p['x'],p['z']] for p in pieces]}}
