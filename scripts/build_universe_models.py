"""Original AI Library orbital archive assets. Run: blender -b --python this_file.

Blender 4.5.3, no add-ons, input meshes, textures, fonts or third-party artwork.
Objects are deliberately authored by family, then merged by material/rotor.
"""
import bpy
import json
import math
import struct
from pathlib import Path
from mathutils import Vector

OUT = Path(__file__).resolve().parents[1] / 'frontend' / 'assets' / 'models'
TAU = math.tau
MATS = {}
PARTS = []

def material(name, color, metal=.4, rough=.35, emission=0):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1)
    m.use_nodes = True
    p = m.node_tree.nodes.get('Principled BSDF')
    p.inputs['Base Color'].default_value = (*color, 1)
    p.inputs['Metallic'].default_value = metal
    p.inputs['Roughness'].default_value = rough
    if emission:
        p.inputs['Emission Color'].default_value = (*color, 1)
        p.inputs['Emission Strength'].default_value = emission
    return m

def start(palette):
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    PARTS.clear()
    MATS.clear()
    for key, args in palette.items():
        MATS[key] = material(key, *args)

def finish_obj(o, name, mat, rotor=False, smooth=False):
    o.name = name
    o.data.materials.append(MATS[mat])
    o['rotor'] = rotor
    for p in o.data.polygons:
        p.use_smooth = smooth
    PARTS.append(o)
    return o

def mesh(name, verts, faces, mat, rotor=False, smooth=False):
    d = bpy.data.meshes.new(name)
    d.from_pydata(verts, [], faces)
    d.update()
    o = bpy.data.objects.new(name, d)
    bpy.context.collection.objects.link(o)
    return finish_obj(o, name, mat, rotor, smooth)

def box(name, loc, scale, mat, rotation=(0, 0, 0), bevel=.035, rotor=False):
    bpy.ops.mesh.primitive_cube_add(size=2, location=loc, rotation=rotation)
    o = bpy.context.object
    o.scale = scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    if bevel:
        mod = o.modifiers.new('machined edges', 'BEVEL')
        mod.width = bevel
        mod.segments = 2
        bpy.ops.object.modifier_apply(modifier=mod.name)
    return finish_obj(o, name, mat, rotor)

def orb(name, loc, scale, mat, segments=32, rings=16, rotor=False):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=segments, ring_count=rings, radius=1, location=loc)
    o = bpy.context.object
    o.scale = scale
    return finish_obj(o, name, mat, rotor, True)

def rod(name, a, b, r, mat, sides=10, rotor=False):
    a, b = Vector(a), Vector(b)
    bpy.ops.mesh.primitive_cylinder_add(vertices=sides, radius=r, depth=(b-a).length, location=(a+b)/2)
    o = bpy.context.object
    o.rotation_mode = 'QUATERNION'
    o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(b-a)
    bpy.context.view_layer.update()
    return finish_obj(o, name, mat, rotor, True)

def ring(name, radius, width, z, mat, segments=64, tilt=(0,0,0), rotor=False, aspect=1):
    verts=[]
    faces=[]
    for i in range(segments):
        a=i/segments*TAU
        for j in range(6):
            b=j/6*TAU
            r=radius+width*math.cos(b)
            verts.append((r*math.cos(a), r*math.sin(a)*aspect, z+width*math.sin(b)))
    for i in range(segments):
        for j in range(6):
            a=i*6+j; b=((i+1)%segments)*6+j
            faces.append((a,b,((i+1)%segments)*6+(j+1)%6,i*6+(j+1)%6))
    o=mesh(name, verts, faces, mat, rotor, True)
    o.rotation_euler=tilt
    return o

def crystal(name, loc, radius, height, mat, sides=6, twist=0):
    verts=[(0,0,height*.57)]
    for z, r, offset in [(height*.16,radius,0),(-height*.31,radius*.78,twist)]:
        for i in range(sides):
            a=i/sides*TAU+offset
            verts.append((r*math.cos(a),r*math.sin(a),z))
    verts.append((0,0,-height*.5))
    faces=[]
    for i in range(sides):
        j=(i+1)%sides
        faces.extend([(0,1+i,1+j),(1+i,1+sides+i,1+sides+j,1+j),
                      (len(verts)-1,1+sides+j,1+sides+i)])
    o=mesh(name,verts,faces,mat)
    o.location=loc
    return o

def panel_patch(name, r, theta, phi, dt, dp, mat, detail=4):
    verts=[]; faces=[]
    for i in range(detail+1):
        t=theta+(i/detail-.5)*dt
        for j in range(detail+1):
            p=phi+(j/detail-.5)*dp
            verts.append((r*math.sin(t)*math.cos(p),r*math.sin(t)*math.sin(p),r*math.cos(t)))
    for i in range(detail):
        for j in range(detail):
            a=i*(detail+1)+j
            faces.append((a,a+1,a+detail+2,a+detail+1))
    return mesh(name,verts,faces,mat,smooth=True)

def loft(name, sections, mat):
    # Four-chine engineered hull, bespoke longitudinal profile.
    verts=[]
    for y,w,z,h in sections:
        verts.extend([(-w,y,z),(-w*.62,y,z+h*.8),(w*.62,y,z+h*.8),
                      (w,y,z),(w*.56,y,z-h*.55),(-w*.56,y,z-h*.55)])
    faces=[tuple(reversed(range(6)))]
    for i in range(len(sections)-1):
        for j in range(6):
            faces.append((i*6+j,i*6+(j+1)%6,(i+1)*6+(j+1)%6,(i+1)*6+j))
    faces.append(tuple(range((len(sections)-1)*6,len(sections)*6)))
    return mesh(name,verts,faces,mat)

WARM={'hull':((.35,.19,.075),.72,.3),'light':((1,.65,.24),.3,.3,1.4),
      'ceramic':((.68,.56,.37),.4,.32),'dark':((.055,.078,.1),.45,.4)}
TECH={'hull':((.12,.27,.35),.65,.29),'light':((.14,.77,.91),.22,.3,1.3),
      'ceramic':((.51,.65,.69),.45,.3),'dark':((.035,.067,.085),.4,.44)}
CREATIVE={'hull':((.43,.17,.13),.4,.35),'light':((1,.59,.28),.2,.35,1.2),
          'ceramic':((.74,.49,.31),.48,.28),'dark':((.11,.08,.065),.4,.4)}
KNOWLEDGE={'hull':((.1,.29,.23),.5,.35),'light':((.37,.91,.68),.2,.3,1.1),
           'ceramic':((.46,.65,.5),.45,.32),'dark':((.03,.09,.075),.4,.4)}
MEMORY={'hull':((.25,.23,.4),.65,.3),'light':((.56,.67,1),.25,.25,1.6),
        'ceramic':((.61,.66,.85),.52,.29),'dark':((.06,.085,.17),.3,.4)}

def personal():
    start(WARM)
    orb('Sunken bronze archive nucleus',(0,0,0),(.81,.81,.81),'dark')
    for row in range(5):
        t=.4+row*.58
        for col in range(12):
            p=col/12*TAU+(row%2)*.14
            panel_patch('Radial archive armor',.84,t,p,.45,.43,'hull')
            if row in (1,3):
                panel_patch('Etched index strips',.853,t,p,.025,.32,'light',2)
    ring('archive_equator',1.13,.085,0,'ceramic',rotor=True)
    ring('archive_rail',1.29,.025,0,'light',rotor=True)
    for i in range(18):
        a=i/18*TAU
        box('Orbiting archive folio',(1.14*math.cos(a),1.14*math.sin(a),0),(.095,.055,.13),
            'hull',(0,0,a),.015,True)
        box('Folio identification',(1.24*math.cos(a),1.24*math.sin(a),.035),(.022,.042,.05),
            'light',(0,0,a),.006,True)
    for s in [-1,1]:
        ring('polar crown',.25,.045,s*.81,'ceramic',32)
        crystal('Polar archive jewel',(0,0,s*.86),.15,.32,'light')

def engineering():
    start(TECH)
    orb('Dark industrial mantle',(0,0,0),(.9,.9,.9),'dark',32,16)
    for row in range(6):
        for col in range(10):
            t=.27+row*.52; p=col/10*TAU+row*.08
            panel_patch('Separated alloy continental plates',.95,t,p,.40,.52,'hull' if (col+row)%3 else 'ceramic')
            if (row+col)%3==0:
                panel_patch('Cyan energy seams',.961,t,p,.035,.32,'light',2)
    for i in range(3):
        a=i/3*TAU
        o=box('Equatorial drydock',(.98*math.cos(a),.98*math.sin(a),0),(.17,.25,.18),'ceramic',(0,0,a),.04)
        box('Drydock recess',(1.14*math.cos(a),1.14*math.sin(a),0),(.025,.18,.08),'dark',(0,0,a),.01)
    rod('North polar tower',(0,0,.86),(0,0,1.23),.13,'hull')
    ring('North polar array',.29,.04,1.13,'light',32)

def creative():
    start(CREATIVE)
    orb('Warm sculpted ceramic world',(0,0,0),(.85,.85,.92),'hull',48,24)
    # Broad offset ribbon continents have lifted edges and organically varying widths.
    for band in range(5):
        verts=[]; faces=[]
        for i in range(73):
            a=i/72*TAU
            center=.38+band*.53+.13*math.sin(a*3+band)
            for side in [-1,1]:
                t=center+side*(.115+.025*math.sin(a*5+band))
                r=.9+.035*math.cos(a*4+band)
                verts.append((r*math.sin(t)*math.cos(a),r*math.sin(t)*math.sin(a),1.03*r*math.cos(t)))
        for i in range(72): faces.append((i*2,i*2+1,i*2+3,i*2+2))
        mesh('Layered terracotta ribbon',verts,faces,'ceramic',smooth=True)
    ring('Slanted outer artist orbit',1.22,.035,0,'light',96,(.36,.3,.15),aspect=.9)
    for i in range(7):
        a=i/7*TAU
        orb('Gilded ribbon clasp',(.83*math.cos(a),.83*math.sin(a),.2*math.sin(a*2)),(.1,.1,.14),'light',12,6)

def knowledge():
    start(KNOWLEDGE)
    orb('Archive biosphere',(0,0,0),(.83,.83,.86),'hull')
    for i in range(8):
        a=i/8*TAU
        for z in [-.43,0,.43]:
            r=.82 if z==0 else .68
            box('Stacked knowledge terracing',(r*math.cos(a),r*math.sin(a),z),(.14,.24,.14),
                'ceramic',(0,0,a),.045)
            box('Terrace manuscript slit',((r+.145)*math.cos(a),(r+.145)*math.sin(a),z),
                (.01,.14,.025),'light',(0,0,a),.005)
    for z in [-.69,.69]: ring('Archive crown',.5,.045,z,'dark',48)
    crystal('North observatory',(0,0,.93),.23,.44,'light')

def note():
    start(TECH)
    crystal('Six sided information prism',(0,0,0),.55,1.9,'hull',6,.08)
    crystal('Exposed luminous archive shard',(0,0,.18),.34,1.5,'light',6)
    for z in [-.38,.18]:
        ring('Hexagonal archive restraint',.56,.045,z,'ceramic',6)
    for i in range(6):
        a=i/6*TAU
        rod('Vertical crystal indexing frame',(.55*math.cos(a),.55*math.sin(a),-.35),
            (.55*math.cos(a),.55*math.sin(a),.22),.025,'ceramic',6)
    for j in range(4):
        box('Offset archive page',(0,-.40-j*.034,-.16+j*.13),(.28,.035,.035),'dark',(.08,0,.08*j),.015)

def memory():
    start(MEMORY)
    crystal('Suspended memory lumen',(0,0,0),.39,1.16,'light',8,.12)
    points=[(0,0,1),(0,0,-1),(.74,0,0),(0,.74,0),(-.74,0,0),(0,-.74,0)]
    for a,b in [(0,i) for i in range(2,6)]+[(1,i) for i in range(2,6)]+[(2,3),(3,4),(4,5),(5,2)]:
        rod('Octahedral memory exoskeleton',points[a],points[b],.055,'ceramic',8)
    for p in points: orb('Memory junction',p,(.115,.115,.115),'hull',12,6)
    for z in [-.28,.28]: ring('Floating memory data rail',.39,.025,z,'light',32)
    for s in [-1,1]:
        box('Memory bank',(s*.54,0,0),(.12,.22,.12),'hull',bevel=.035)
        for j in range(3): box('Memory bank register',(s*.55,-.225,-.06+j*.06),(.085,.012,.009),'light',bevel=0)

def station():
    start(WARM)
    rod('Central six deck station keel',(0,0,-.6),(0,0,.64),.26,'hull',12)
    for z,r in [(-.5,.34),(-.25,.45),(0,.35),(.25,.45),(.5,.34)]:
        rod('Layered command decks',(0,0,z-.045),(0,0,z+.045),r,'ceramic',12)
        ring('Deck navigation windows',r+.004,.016,z,'light',48)
    ring('Rotating outer service rail',.94,.06,0,'hull',72,rotor=True)
    for i in range(6):
        a=i/6*TAU
        rod('Radial access truss',(.3*math.cos(a),.3*math.sin(a),0),(.94*math.cos(a),.94*math.sin(a),0),.065,'ceramic',8,True)
        box('Docking work module',(.91*math.cos(a),.91*math.sin(a),0),(.19,.14,.21),'hull',(0,0,a),.045,True)
        box('Module workstation skylight',(.91*math.cos(a),.91*math.sin(a),.21),(.13,.09,.013),'light',(0,0,a),.015,True)
        # Offset winglets create readable station silhouette beyond the central ring.
        for s in [-1,1]:
            x=1.04*math.cos(a)-s*.22*math.sin(a); y=1.04*math.sin(a)+s*.22*math.cos(a)
            box('Solar service wing',(x,y,0),(.17,.12,.027),'dark',(0,0,a),.008,True)
            for j in range(3):
                box('Solar cell copper bus',(x+(j-1)*.085*math.cos(a),y+(j-1)*.085*math.sin(a),.031),
                    (.01,.1,.007),'ceramic',(0,0,a),0,True)
    crystal('Station navigation crown',(0,0,.73),.12,.28,'light')

def ship(small=False):
    start(TECH)
    loft('Swept communication hull',[(-1.25,.06,0,.07),(-.64,.30,0,.2),(.15,.32,0,.24),
                                     (.66,.23,.015,.2),(.84,.14,.025,.12)],'hull')
    loft('Dorsal ceramic armor',[(-.91,.045,.16,.035),(-.34,.18,.22,.08),(.47,.14,.23,.07)],'ceramic')
    box('Observation bridge',(0,.25,.3),(.16,.22,.065),'dark',bevel=.045)
    box('Forward bridge glass',(0,.01,.32),(.11,.025,.026),'light',bevel=.012)
    for side in [-1,1]:
        x=side*.46
        loft('Swept outrigger nacelle',[( -.48,.075,0,.08),(.33,.11,0,.12),(.96,.08,0,.08)],'ceramic').location.x=x
        box('Wing root truss',(side*.3,.26,0),(.23,.1,.05),'hull',(0,0,side*.23),.02)
        rod('Ion nozzle',(x,.91,0),(x,1.04,0),.065,'dark',12)
        rod('Ion chamber',(x,1.04,0),(x,1.055,0),.047,'light',12)
        for j in range(4): box('Nacelle vent',(x,.38+j*.11,.095),(.05,.023,.013),'dark',bevel=.005)
    for j in range(5): box('Hull inset register',(0,-.64+j*.13,.185),(.04,.016,.014),'light',bevel=.005)
    if not small:
        # Asymmetric side dish and two forked transmission spars.
        rod('Communication mast',(-.15,.4,.32),(-.29,.47,.73),.026,'ceramic',8)
        ring('Dish receiving rim',.22,.025,.76,'ceramic',40).location=(-.29,.47,0)
        orb('Concave signal receiver',(-.29,.47,.73),(.2,.2,.042),'dark',24,8)
        rod('Dish feed horn',(-.29,.47,.74),(-.29,.47,.91),.018,'light',8)
        for x in [.13,.28]: rod('Fork transmitter',(x,.57,.28),(x,.8,.68),.018,'ceramic',8)
        box('Offset equipment deck',(.37,.44,.19),(.2,.17,.09),'hull',bevel=.04)
    else:
        rod('Compact uplink',(0,.43,.32),(0,.51,.61),.018,'ceramic',8)
        orb('Uplink beacon',(0,.51,.62),(.055,.055,.055),'light',12,6)

def module():
    start(WARM)
    box('Tool cartridge body',(0,0,0),(.46,.33,.36),'hull',bevel=.10)
    for x in [-.43,.43]:
        box('Machined side grip',(x,0,0),(.075,.39,.28),'ceramic',bevel=.04)
        for z in [-.14,0,.14]: box('Grip machining channels',(x,-.397,z),(.06,.01,.015),'dark',bevel=.005)
    box('Tool operation panel',(0,-.342,.02),(.3,.016,.20),'dark',bevel=.025)
    for i in range(3): box('Tool readiness registers',(-.18+i*.18,-.365,.04),(.045,.009,.09),'light',bevel=.012)
    for x in [-.26,0,.26]: rod('Tool coupling pin',(x,0,.35),(x,0,.61),.045,'ceramic',8)
    box('Cartridge key',(0,0,-.45),(.15,.2,.12),'dark',bevel=.025)

def export_asset(name, family, detail):
    # Flatten detailed sub-parts into <= 8 material batches. Rotors stay separate.
    groups={}
    for o in PARTS:
        groups.setdefault((o.data.materials[0].name, bool(o.get('rotor'))), []).append(o)
    merged=[]
    for (mat,rotor), objects in groups.items():
        bpy.ops.object.select_all(action='DESELECT')
        for o in objects: o.select_set(True)
        bpy.context.view_layer.objects.active=objects[0]
        if len(objects) > 1:
            bpy.ops.object.join()
        o=bpy.context.object
        bpy.context.scene.cursor.location=(0,0,0)
        bpy.ops.object.origin_set(type='ORIGIN_CURSOR')
        o.name=('idle_rotor_' if rotor else 'body_')+mat
        merged.append(o)
    all_points=[o.matrix_world@v.co for o in merged for v in o.data.vertices]
    lo=Vector(tuple(min(p[i] for p in all_points) for i in range(3)))
    hi=Vector(tuple(max(p[i] for p in all_points) for i in range(3)))
    center=(lo+hi)/2
    radius=max((p-center).length for p in all_points)
    for o in merged:
        # Bake common center/scale so every exported asset has radius exactly one.
        transform=o.matrix_world.copy()
        for v in o.data.vertices: v.co=(transform@v.co-center)/radius
        o.matrix_world.identity()
    path=OUT/(name+'.glb')
    bpy.ops.export_scene.gltf(filepath=str(path),export_format='GLB',use_selection=False,
        export_yup=True,export_normals=True,export_materials='EXPORT',export_animations=False,
        export_cameras=False,export_lights=False,export_extras=False)
    raw=path.read_bytes(); size=struct.unpack_from('<I',raw,12)[0]
    doc=json.loads(raw[20:20+size])
    primitives=[p for m in doc['meshes'] for p in m['primitives']]
    return {'name':name,'file':path.name,'family':family,'description':detail,
        'triangles':sum(doc['accessors'][p['indices']]['count']//3 for p in primitives),
        'materials':len(doc['materials']),'drawCalls':len(primitives),'bytes':len(raw),
        'nominalRadius':1,'origin':'Original procedural mesh authored for AI Library; no external artwork'}

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    builds=[('personal-core','personal',personal,'Bronze archive world with indexed armor, copper folios and orbiting archive rail'),
      ('project-engineering','project',engineering,'Segmented industrial world, equatorial drydocks and polar antenna'),
      ('project-creative','project',creative,'Terracotta ribbon world with lifted sculptural continents'),
      ('project-knowledge','project',knowledge,'Jade manuscript terraces with polar observatory'),
      ('note-crystal','note',note,'Restrained six-sided archive shard with indexed page stack'),
      ('memory-core','memory',memory,'Suspended lumen in octahedral lattice with memory banks'),
      ('skill-station','skill',station,'Six-deck radial station, dock modules and solar service wings'),
      ('mcp-ship','mcp',ship,'Swept asymmetric communications frigate with receiving dish'),
      ('skill-module','skill',module,'Compact tool cartridge with grip channels and coupling pins'),
      ('mcp-vessel','mcp',lambda:ship(True),'Compact twin-nacelle uplink vessel')]
    assets=[]
    for name,family,build,detail in builds:
        build()
        assets.append(export_asset(name,family,detail))
    manifest={'version':1,'authoring':'scripts/build_universe_models.py',
        'blenderVersion':bpy.app.version_string,'license':'Original project assets; no third-party artwork',
        'assets':assets}
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2)+'\n',encoding='utf-8')
    print('EXPORTED',len(assets),'assets',sum(a['bytes'] for a in assets),'bytes')

if __name__=='__main__': main()
