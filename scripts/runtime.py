#!/usr/bin/env python3
"""Deterministic Explicit Info project/state/validation adapter; business judgment stays with the model."""
import argparse, copy, datetime, hashlib, json, os, re, subprocess, sys, tempfile
from pathlib import Path
from urllib.parse import urlparse,parse_qs,urlencode

ROOT=Path(__file__).resolve().parent.parent
DIMENSIONS=('breadth','thickness','depth')
SCOPE_FIELDS=('evaluation_scope','scope_basis','shared_boundaries','explicit_exclusions','exclusions_status')

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def read(path):return json.loads(Path(path).read_text())
def digest(obj):return hashlib.sha256(json.dumps(obj,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def save(path,obj):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix=path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as f:json.dump(obj,f,ensure_ascii=False,indent=2);f.write('\n')
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)
def require(condition,message):
    if not condition:raise ValueError(message)
def nonempty(value):return isinstance(value,str) and bool(value.strip())
def project_key(source,round_id='initial'):
    require(nonempty(source.get('canonical_id')),'Canonical source document identity is required; resolve wiki aliases first')
    return 'explicit-'+hashlib.sha256((source['canonical_id']+'\n'+round_id).encode()).hexdigest()[:32]

def validate_features(features):
    require(isinstance(features,list) and bool(features),'Empty feature catalog')
    seen=set()
    for f in features:
        require(nonempty(f.get('id')) and f['id'] not in seen,'Feature id missing/duplicated');seen.add(f['id'])
        require(nonempty(f.get('name')),'Feature name missing')
        require(nonempty(f.get('priority')),'Feature priority missing; use 未标注 when not supplied')
        require(bool(f.get('sourceRef')),'Feature sourceRef missing: '+f['id'])
        if f['priority']=='P0':require(bool(f.get('priorityEvidence')),'P0 evidence missing: '+f['id'])
        if any(k in f for k in ('evaluationClass','evaluationReason','defaultSelected')):
            require(f.get('evaluationClass')=='business','Non-business items must be removed before featureCatalog: '+f['id'])
            require(nonempty(f.get('evaluationReason')),'Model evaluation reason missing: '+f['id'])
            require(f.get('defaultSelected') is True,'Retained business functions must be selected by default: '+f['id'])
    return seen

def validate_tree(features,abilities,expected):
    require(isinstance(abilities,list),'Abilities must be an array')
    covered=set();ids=set();names=set();need_ids=set();warnings=[]
    for a in abilities:
        require(nonempty(a.get('id')) and a['id'] not in ids,'Ability id missing/duplicated');ids.add(a['id'])
        require(nonempty(a.get('name')) and a['name'].strip() not in names,'Ability name missing/duplicated');names.add(a['name'].strip())
        fids=a.get('featureIds');require(isinstance(fids,list) and bool(fids),'Ability has no feature links: '+a['id'])
        require(len(set(fids))==len(fids),'Duplicate feature links: '+a['id'])
        require(set(fids)<=expected,'Out-of-scope feature links: '+str(set(fids)-expected));covered.update(fids)
        needs=a.get('needs');require(isinstance(needs,list) and bool(needs),'Needs missing: '+a['id']);mapped=set()
        for n in needs:
            require(nonempty(n.get('id')) and n['id'] not in need_ids,'Need id missing/duplicated');need_ids.add(n['id'])
            require(nonempty(n.get('name')),'Need name missing')
            if len(n['name'])>8:warnings.append('Inspect long need label without mechanically truncating: '+n['name'])
            if 'featureIds' in n:
                require(isinstance(n['featureIds'],list) and bool(n['featureIds']),'Need feature links empty: '+n['id'])
                require(set(n['featureIds'])<=set(fids),'Need links outside parent: '+n['id']);mapped.update(n['featureIds'])
        if any('featureIds' in n for n in needs):require(mapped==set(fids),'Need mapping omits parent features: '+a['id'])
    require(covered==expected,'Unmapped features: '+str(expected-covered))
    return warnings

def validate_extraction_review(snapshot):
    if snapshot.get('extractionMethod')=='model_facts':
        audit=snapshot.get('extractionAudit',[])
        require(isinstance(audit,list) and bool(audit),'Model-extracted facts require a completed extractionAudit before project creation')
        require(all(isinstance(a.get('valid_features'),int) and not isinstance(a.get('valid_features'),bool) and a['valid_features']>=0 and nonempty(a.get('note')) for a in audit),'Each model-fact audit needs valid_features count and a source-completeness note')
        require(sum(a['valid_features'] for a in audit)==len(snapshot['featureCatalog']),'Model-fact audit count differs from final feature catalog')
    review=snapshot.get('extractionReview',{})
    issues=review.get('issues',[]);decisions=review.get('decisions',[])
    require(isinstance(issues,list) and isinstance(decisions,list),'Invalid extractionReview')
    by_id={d.get('issue_id'):d for d in decisions}
    require(len(by_id)==len(decisions),'Duplicate extraction review decisions')
    require(set(by_id)<={i['id'] for i in issues},'Review decision refers to unknown issue')
    features={f['id']:f for f in snapshot['featureCatalog']}
    for issue in issues:
        require(issue['id'] in by_id,'Unresolved multi-part feature cell: '+issue['id']+'; model must distinguish separate features from notes')
        d=by_id[issue['id']];require(d.get('decision') in ('single_feature_with_notes','split_features'),'Unknown extraction review decision')
        require(nonempty(d.get('rationale')),'Extraction review rationale required')
        ids=d.get('feature_ids');require(isinstance(ids,list) and bool(ids) and len(set(ids))==len(ids),'Review decision needs unique feature_ids')
        require(set(ids)<=set(features),'Review refers to unknown output features')
        if d['decision']=='single_feature_with_notes':require(ids==[issue['provisional_feature_id']],'Single-feature decision must preserve the provisional source feature')
        else:require(len(ids)>=2,'Split decision needs at least two source-backed features')
        for fid in ids:
            ref=features[fid]['sourceRef'];original=issue['sourceRef']
            require(ref['document_id']==original['document_id'],'Review feature belongs to another source')
            candidate_ids={c.get('block_id') for c in issue.get('candidates',[]) if c.get('block_id')}
            same_row=ref.get('table_id')==original.get('table_id') and ref.get('row')==original.get('row') and ref.get('table_id') is not None
            require(same_row or ref.get('block_id') in candidate_ids,'Review feature does not trace to the ambiguous cell')

def validate_presets(snapshot,model):
    validate_extraction_review(snapshot)
    features=snapshot['featureCatalog'];validate_features(features); warnings=[]
    for scope in ('p0','all'):
        expected={f['id'] for f in features if scope=='all' or f['priority']=='P0'}
        tree=model.get('abilitiesByScope',{}).get(scope)
        warnings.extend(validate_tree(features,tree,expected))
        for ability in tree:
            require(all(n.get('featureIds') for n in ability['needs']),'Model-generated needs require featureIds for three-level traceability: '+ability['id'])
    return {'ok':True,'warnings':warnings,'counts':{'all':len(features),'p0':sum(f['priority']=='P0' for f in features)}}

def validate_bmk(plan,scope=None,scenes=None,snapshot=None):
    if isinstance(scope,dict):scope=scope.get('evaluation_scope') or scope.get('abilities')
    if isinstance(scenes,dict):scenes=scenes.get('user_scenarios')
    if scope is not None:require(isinstance(scope,list),'Scope must be an ability list or {evaluation_scope:[...]}')
    if scenes is not None:require(isinstance(scenes,list),'Scenes must be an array or {user_scenarios:[...]}')
    def count(v):require(isinstance(v,int) and not isinstance(v,bool) and v>=0,'Counts must be nonnegative integers');return v
    total=count(plan['total']);require(total>0,'Total must be positive')
    dims=plan['dimensions'];require(set(dims)==set(DIMENSIONS),'Expected breadth/thickness/depth');require(sum(count(dims[k]) for k in DIMENSIONS)==total,'Dimension sum differs from total')
    users=plan['user_scenarios'];abilities=plan['abilities']
    require(users and abilities,'Scene and ability allocations required')
    require(len({x['id'] for x in users})==len(users),'Duplicate user scene ids')
    require(len({x['id'] for x in abilities})==len(abilities),'Duplicate ability ids')
    require(sum(count(x['count']) for x in users)==total,'User scene sum differs')
    require(sum(count(x['count']) for x in abilities)==total,'Ability sum differs')
    for a in abilities:
        require(nonempty(a.get('name')),'Ability allocation name missing')
        require(set(a['dimensions'])==set(DIMENSIONS),'Ability dimensions missing')
        require(sum(count(a['dimensions'][k]) for k in DIMENSIONS)==a['count'],'Ability dimension sum differs: '+a['name'])
        require(bool(a['task_scenarios']),'Task scenes missing: '+a['name'])
        require(len({x['id'] for x in a['task_scenarios']})==len(a['task_scenarios']),'Duplicate task scene ids')
        require(sum(count(x['count']) for x in a['task_scenarios'])==a['count'],'Task scene sum differs: '+a['name'])
    for k in DIMENSIONS:require(sum(a['dimensions'][k] for a in abilities)==dims[k],'Dimension marginal mismatch: '+k)
    if scope:require([a['name'] for a in abilities]==[a['name'] for a in scope],'Ability names/order differ from confirmed scope')
    if scenes:require([x['name'] for x in users]==[x['name'] for x in scenes],'User scene names/order differ from confirmed proposal')
    # Joint allocation cells keep all marginals tied to one plan without generating every query.
    require(isinstance(plan.get('cells'),list) and bool(plan['cells']),'Joint allocation cells are required for consistent task/scene/dimension attribution')
    if 'cells' in plan:
        cells=plan['cells'];require(sum(count(c['count']) for c in cells)==total,'Joint cells total differs')
        uid={x['id'] for x in users};amap={a['id']:a for a in abilities}
        for c in cells:
            require(c['ability_id'] in amap and c['user_scenario_id'] in uid and c['dimension'] in DIMENSIONS,'Unknown cell category')
            require(c['task_scenario_id'] in {t['id'] for t in amap[c['ability_id']]['task_scenarios']},'Task scene outside cell parent')
        for u in users:require(sum(c['count'] for c in cells if c['user_scenario_id']==u['id'])==u['count'],'Cell user-scene mismatch')
        for a in abilities:
            for d in DIMENSIONS:require(sum(c['count'] for c in cells if c['ability_id']==a['id'] and c['dimension']==d)==a['dimensions'][d],'Cell ability-dimension mismatch')
            for t in a['task_scenarios']:require(sum(c['count'] for c in cells if c['ability_id']==a['id'] and c['task_scenario_id']==t['id'])==t['count'],'Cell task-scene mismatch')
    warnings=[]
    selected=None
    if scope and any('feature_ids' in a or 'featureIds' in a for a in scope):
        require(all(isinstance(a.get('feature_ids',a.get('featureIds')),list) for a in scope),'All scope rows must provide feature_ids when using source coverage validation')
        selected={fid for a in scope for fid in a.get('feature_ids',a.get('featureIds',[]))}
    known={f['id'] for f in snapshot['featureCatalog']} if snapshot else selected
    if snapshot and selected is not None:require(selected<=known,'Scope refers to unknown source features')
    if 'source_feature_ids' in plan:
        require(isinstance(plan['source_feature_ids'],list),'source_feature_ids must be an array')
        if known is not None:require(set(plan['source_feature_ids'])<=known,'Plan source_feature_ids include unknown features')
        if selected is not None:require(set(plan['source_feature_ids'])==selected,'Plan source_feature_ids differ from actual selected scope')
    if selected is not None and ('source_feature_ids' in plan or 'coverage_map' in plan):
        require(isinstance(plan.get('coverage_map'),list) and bool(plan['coverage_map']),'Selected features require an actual coverage_map, not an ID list alone')
    if 'coverage_map' in plan:
        require(isinstance(plan['coverage_map'],list),'coverage_map must be an array')
        task_index={}
        for ability in abilities:
            for task in ability['task_scenarios']:task_index.setdefault(task['id'],[]).append(ability['id'])
        covered=set()
        for entry in plan['coverage_map']:
            fid=entry.get('feature_id');require(nonempty(fid),'Coverage feature_id missing')
            if known is not None:require(fid in known,'Coverage refers to unknown/out-of-scope source feature: '+fid)
            if selected is not None:require(fid in selected,'Coverage feature is not selected: '+fid)
            tasks=entry.get('task_scenario_ids')
            if tasks is None and entry.get('breadth_task_scenario_id'):tasks=[entry['breadth_task_scenario_id']]
            require(isinstance(tasks,list) and bool(tasks) and all(nonempty(t) for t in tasks),'Coverage entry requires nonempty task_scenario_ids')
            for tid in tasks:
                require(tid in task_index,'Coverage refers to unknown task scene: '+tid)
                parents=task_index[tid]
                if entry.get('ability_id'):parents=[aid for aid in parents if aid==entry['ability_id']]
                require(len(parents)==1,'Coverage task scene parent is ambiguous/invalid: '+tid)
                require(any(c['task_scenario_id']==tid and c['ability_id']==parents[0] and c['dimension']=='breadth' and c['count']>0 for c in cells),'Coverage task has no positive breadth allocation: '+tid)
            covered.add(fid)
        if selected is not None:require(covered==selected,'Coverage omits selected source features: '+str(selected-covered))
        warnings.append('Coverage IDs and positive breadth allocations are structurally valid only. The model must verify actual business meaning and meaningful difficulty; necessary business conditions belong to real tasks, while engineering checks and product strategies are not independent features. Coverage does not require one question per feature.')
    elif selected is not None:
        warnings.append('Selected feature IDs are available but no coverage_map was supplied; numeric consistency does not prove business coverage.')
    return {'ok':True,'total':total,'dimensions':dims,'task_scene_count':sum(len(a['task_scenarios']) for a in abilities),'warnings':warnings}

def prepare(snapshot,model,obj,tool,round_id,version):
    validate_presets(snapshot,model)
    require(nonempty(obj) and nonempty(tool),'Object and tool type must be identified by the model')
    require(all(f.get('evaluationClass')=='business' and f.get('defaultSelected') is True for f in snapshot['featureCatalog']),
            'Before a new project, the model must retain only actual service business functions and explain each decision; no engineering tests or product strategies')
    variants={scope:{'abilities':copy.deepcopy(model['abilitiesByScope'][scope]),'coverage':'all','excludedFeatureIds':[],'exclusionNote':''} for scope in ('p0','all')}
    default=variants['p0']
    return {'projectKey':project_key(snapshot['source_document'],round_id),'evaluationObject':obj,'toolType':tool,'version':version,'prdUrl':snapshot['source_document']['url'],'featureCatalog':snapshot['featureCatalog'],'scopeMode':'p0','coverageMode':default['coverage'],'excludedFeatureIds':default['excludedFeatureIds'],'exclusionNote':default['exclusionNote'],'abilities':default['abilities'],'scopeVariants':variants,'status':'draft','sourceDocument':snapshot['source_document'],'round':round_id}

COLUMNS={'projectKey':'project_key','evaluationObject':'evaluation_object','toolType':'tool_type','version':'version','prdUrl':'prd_url','featureCatalog':'feature_catalog','scopeMode':'scope_mode','coverageMode':'coverage_mode','excludedFeatureIds':'excluded_feature_ids','exclusionNote':'exclusion_note','abilities':'abilities','scopeVariants':'scope_variants','status':'status'}
JSON_FIELDS={'featureCatalog','excludedFeatureIds','abilities','scopeVariants'}

def quote(value):
    # PostgreSQL dollar quoting avoids dependence on standard_conforming_strings.
    v=str(value);tag='q_'+hashlib.sha256(v.encode()).hexdigest()[:16]
    while '$'+tag+'$' in v:tag+='x'
    return '$'+tag+'$'+v+'$'+tag+'$'
def sqlvalue(value,is_json=False):return quote(json.dumps(value,ensure_ascii=False) if is_json else value)+('::jsonb' if is_json else '')
def connection(args=None):
    conf=read(getattr(args,'connection',None) or ROOT/'references/rules/application.json')
    require(re.fullmatch(r'app_[a-zA-Z0-9]+',conf['app_id']) is not None,'Invalid app id')
    require(conf.get('table','evaluation_range_state')=='evaluation_range_state','Adapter requires evaluation_range_state schema')
    require(urlparse(conf['app_url']).scheme=='https' and urlparse(conf['app_url']).path.rstrip('/').endswith('/'+conf['app_id']),'App URL does not match app id')
    return conf

def connection_binding(args=None):
    conf=connection(args)
    return {'app_id':conf['app_id'],'environment':conf.get('environment','online')}

def check_state_connection(state,args,remote=False,allow_legacy=False):
    is_prd=state.get('input_document_type')=='prd' or state.get('scope_origin')=='pm_form'
    require(is_prd or not remote,'Description-only state cannot be used for an application project; use an independent --state-root')
    if not is_prd:return False
    current=connection_binding(args);bound=state.get('connection_binding')
    if bound is not None:
        require(bound==current,'Project state belongs to another app/environment; use its original connection or an independent --state-root')
        return False
    legacy_path=getattr(args,'legacy_connection',None) if allow_legacy else None
    require(legacy_path,'PRD state has no verified connection_binding; first use read-project --legacy-connection <original recorded connection JSON> to verify its origin, or use an independent --state-root. Do not recreate history from the current connection')
    old=connection(argparse.Namespace(connection=legacy_path))
    require(nonempty(old.get('environment')),'Original connection evidence must explicitly identify its environment; use verified original records or an independent --state-root')
    require({'app_id':old['app_id'],'environment':old['environment']}==current,'Original connection evidence differs from the selected app/environment; use the original connection or an independent --state-root')
    require(nonempty(state.get('project_url')) and urlparse(state['project_url']).scheme=='https','Legacy state lacks its original project URL; verify its origin or use an independent --state-root')
    require(extract_key(state['project_url'],args)==state['project_key'],'Legacy project URL does not match its state; verify its origin or use an independent --state-root')
    return True

def bind_legacy_state(state,remote,args):
    previous=state.get('latest_remote') or state.get('confirmed_range') or {}
    fields=('projectKey','prdUrl','createdAt')
    require(all(nonempty(previous.get(field)) and previous[field]==remote.get(field) for field in fields),
            'Legacy project identity/source/creation evidence does not match the read-only result; verify original records or use an independent --state-root')
    require(remote['projectKey']==state['project_key'],'Legacy remote project identity mismatch; use an independent --state-root')
    state['connection_binding']=connection_binding(args)
    event(state,'legacy_connection_verified',{'connection_binding':state['connection_binding'],'evidence_file':str(Path(args.legacy_connection).resolve()),'verification':'original connection record, saved project URL, and read-only project/source/creation match'})

def execute_sql(sql,args):
    conf=connection(args);cli=args.cli or os.environ.get('LARK_CLI','lark-cli')
    argv=[cli,'apps','+db-execute','--app-id',conf['app_id'],'--environment',conf.get('environment','online'),'--sql','-','--as','user','--yes','--format','json']
    run=subprocess.run(argv,input=sql,text=True,capture_output=True,timeout=180)
    stream=run.stdout if run.returncode==0 else run.stderr or run.stdout
    try:envelope=json.loads(stream)
    except ValueError:raise RuntimeError('CLI returned an unreadable envelope; preserve operation and inspect safe error output')
    if run.returncode or envelope.get('ok') is not True:
        err=envelope.get('error',{});raise RuntimeError(json.dumps({'exit_code':run.returncode,'type':err.get('type'),'subtype':err.get('subtype'),'message':err.get('message'),'hint':err.get('hint')},ensure_ascii=False))
    return envelope.get('data')

def select_project(key,args):
    cols=', '.join(COLUMNS.values())+', _updated_at, _created_at'
    rows=execute_sql('SELECT '+cols+' FROM evaluation_range_state WHERE project_key = '+quote(key)+';',args)
    require(isinstance(rows,list),'Unexpected SELECT result')
    require(len(rows)<=1,'Duplicate project key in remote table')
    if not rows:return None
    row=rows[0];out={k:row.get(v) for k,v in COLUMNS.items()}
    for k in JSON_FIELDS:
        if isinstance(out[k],str):out[k]=json.loads(out[k])
    out['updatedAt']=row.get('_updated_at');out['createdAt']=row.get('_created_at');return out

def url_for(key,args):
    url=connection(args)['app_url'];return url+('&' if '?' in url else '?')+urlencode({'project':key})

def extract_key(value,args):
    if not value.startswith('http'):require(re.fullmatch(r'[A-Za-z0-9_\-\u4e00-\u9fff]+',value),'Invalid project key');return value
    u=urlparse(value);base=urlparse(connection(args)['app_url']);require(u.netloc==base.netloc and u.path.rstrip('/')==base.path.rstrip('/'),'Project URL is not this configured application')
    keys=parse_qs(u.query).get('project',[]);require(len(keys)==1,'Project URL missing unique project key');return keys[0]

def state_path(args,key):
    require(re.fullmatch(r'[A-Za-z0-9_\-\u4e00-\u9fff]+',key),'Unsafe project key')
    return Path(args.state_root)/key/'state.json'
def state_load(args,key):return read(state_path(args,key))

def pick_fields(data,keys):
    return {key:data[key] for key in keys if key in data}

def state_view(state,path,section=None):
    """Read current workflow context without loading source bodies or event history."""
    pending=state.get('pending') or {}
    confirmations=state.get('confirmations') or {}
    identity=pick_fields(state,('project_key','input_document_type','scope_origin','round','stage','updated_at'))
    is_prd=state.get('input_document_type')=='prd' or state.get('scope_origin')=='pm_form'
    identity['connection_binding']=state.get('connection_binding') if is_prd else None
    identity['connection_binding_status']=('bound' if state.get('connection_binding') else 'missing') if is_prd else 'not_applicable'
    source=state.get('source_document') or {}
    identity['source_document']=pick_fields(source,('canonical_id','document_id','title','url','path','revision','revision_id'))
    identity['state_file']=str(Path(path).resolve())
    flags={key:state.get(key) for key in ('range_ready','user_scenarios_confirmed','bmk_plan_confirmed')}
    identity['confirmation_flags']=flags
    identity['outputs_stale']=state.get('outputs_stale')
    if section:
        fields={
            'scope':('evaluation_scope','scope_basis','shared_boundaries','explicit_exclusions','exclusions_status','scope_revision','presentation_scope','presentation_revision','range_confirmation','confirmed_range_digest','snapshot_digest'),
            'scenarios':('user_scenarios',),
            'bmk':('bmk_plan',),
            'outputs':('outputs',),
        }
        require(section in fields,'Unknown state section')
        data=pick_fields(state,fields[section])
        if section=='scope' and state.get('confirmed_range'):
            data['confirmed_range']=pick_fields(state['confirmed_range'],('scopeMode','coverageMode','excludedFeatureIds','exclusionNote','abilities','status','updatedAt'))
        if section in ('scope','scenarios','bmk'):
            if section in confirmations:data['confirmation']=confirmations[section]
            if pending.get('kind')==section:data['pending']=pending
        return {**identity,'view':section,'data':data}

    outputs=state.get('outputs') or {}
    references={}
    for key,value in outputs.items():
        if not (key.endswith('_evidence') or key.endswith('_review') or key.endswith('_operations') or key=='latest_presentation_readback'):
            continue
        if isinstance(value,str) and value.strip():
            if urlparse(value).scheme:
                references[key]={'reference':value,'kind':'external','verified':False}
            else:
                local=Path(value).expanduser()
                try:exists=local.is_file()
                except OSError:exists=False
                path_like=local.is_absolute() or value.startswith(('./','../','~/')) or bool(re.fullmatch(r'\.[A-Za-z0-9]{1,10}',local.suffix))
                if path_like or exists:
                    references[key]={'reference':value,'kind':'local','resolved_path':str(local.resolve()),'exists':exists}
                    if not local.is_absolute():references[key]['relative_to']=str(Path.cwd())
                else:
                    references[key]={'kind':'embedded','state_field':'outputs.'+key,'verified':False}
        else:
            references[key]={'kind':'embedded' if value else 'missing','state_field':'outputs.'+key}
    required=('document_id','document_url','board_id','readback_evidence','board_preview_evidence','showcase_quality_review')
    brief_confirmations={kind:pick_fields(value,('proposal_id','origin','at')) for kind,value in confirmations.items() if kind in ('scenarios','bmk') and isinstance(value,dict)}
    if isinstance(state.get('range_confirmation'),dict):brief_confirmations['range']=pick_fields(state['range_confirmation'],('origin','at','range_digest'))
    return {
        **identity,'view':'summary',
        'versions':pick_fields(state,('scope_revision','presentation_revision','snapshot_digest','confirmed_range_digest')),
        'pending':pick_fields(pending,('kind','proposal_id','created_at')) or None,
        'confirmations':brief_confirmations,
        'outputs':pick_fields(outputs,('document_id','document_url','board_id','board_status','completion_status','remaining','latest_revision','scope_revision','board_plan_revision')),
        'evidence_references':references,
        'missing_completion_fields':['outputs.'+key for key in required if not outputs.get(key)],
        'missing_local_evidence':[{'field':'outputs.'+key,'path':value['resolved_path']} for key,value in references.items() if value.get('kind')=='local' and not value['exists']],
        'available_sections':{
            'scope':bool(state.get('evaluation_scope') or state.get('confirmed_range')),
            'scenarios':bool(state.get('user_scenarios') or pending.get('kind')=='scenarios'),
            'bmk':bool(state.get('bmk_plan') or pending.get('kind')=='bmk'),
            'outputs':bool(outputs),
        },
        'evidence_note':'Local checks report file presence only; external references are not fetched. Summary flags do not prove semantic or visual acceptance.',
    }

def event(state,kind,detail):state.setdefault('events',[]).append({'at':now(),'kind':kind,'detail':detail});state['updated_at']=now()
def initial_state(project,snapshot,binding=None):
    return {'schema_version':1,'project_key':project['projectKey'],'source_document':snapshot['source_document'],'input_document_type':'prd','scope_origin':'pm_form','connection_binding':binding if binding is not None else connection_binding(),'round':project['round'],'stage':'pm_pending','range_ready':False,'user_scenarios_confirmed':False,'bmk_plan_confirmed':False,'snapshot_digest':digest(snapshot),'initial_project_digest':digest({k:project[k] for k in COLUMNS}),'initial_project':project,'events':[],'outputs':{}}

def validate_scope_rendering(scope,state):
    require(isinstance(scope,list) and bool(scope),'Scope must be a nonempty ability list')
    for item in scope:
        require(nonempty(item.get('name')),'Missing scope field: name')
        for field in ('user_needs','scope_points','source_refs'):
            require(isinstance(item.get(field),list) and bool(item[field]),'Missing scope list: '+field)
        require(all(nonempty(n) for n in item['user_needs']),'User needs must be nonempty labels')
    if state['input_document_type']=='prd':
        confirmed=state.get('confirmed_range',{})
        require(confirmed.get('status')=='confirmed','Actual confirmed PM range required')
        abilities=confirmed['abilities']
        require([x['name'] for x in scope]==[x['name'] for x in abilities],'Preserve confirmed ability names and order')
        for item,ability in zip(scope,abilities):
            require(item['user_needs']==[n['name'] for n in ability['needs']],'Preserve confirmed user need names and order: '+ability['name'])
            for field in ('feature_ids','featureIds'):
                if field in item:
                    ids=item[field]
                    require(isinstance(ids,list) and all(nonempty(fid) for fid in ids) and len(set(ids))==len(ids),'Invalid scope feature links: '+ability['name'])
                    require(set(ids)==set(ability['featureIds']),'Preserve confirmed feature links: '+ability['name'])

def scope_structure(scope):
    # Text equivalence remains a model judgment; this only guards labels and links.
    return [{'name':item['name'],'user_needs':item['user_needs'],
             **{field:sorted(item[field]) for field in ('feature_ids','featureIds') if field in item}}
            for item in scope]

def queue_description_scope(state):
    data=copy.deepcopy(pick_fields(state,SCOPE_FIELDS))
    previous=state.pop('range_confirmation',None)
    if previous:state.setdefault('range_confirmation_history',[]).append(previous)
    state.setdefault('confirmations',{}).pop('scope',None)
    state.update(range_ready=False,user_scenarios_confirmed=False,bmk_plan_confirmed=False,stage='scope_confirmation_pending')
    state['pending']={'kind':'scope','proposal_id':digest(data),'data':data,'created_at':now()}
    event(state,'description_scope_proposed',{'proposal_id':state['pending']['proposal_id']})

def import_confirmed(state,remote,confirmation=None):
    require(remote['status']=='confirmed','Remote project is not confirmed')
    scope=remote['scopeMode'];require(scope in ('p0','all'),'Invalid confirmed scope')
    allowed={f['id'] for f in remote['featureCatalog'] if scope=='all' or f['priority']=='P0'}
    excluded=set(remote['excludedFeatureIds'] or [])
    require(excluded<=allowed,'Excluded ids outside selected scope')
    require(remote['coverageMode'] in ('all','partial'),'Unknown coverage mode')
    if remote['coverageMode']=='all':require(not excluded,'Complete coverage cannot exclude features')
    validate_tree(remote['featureCatalog'],remote['abilities'],allowed-excluded)
    old=state.get('confirmed_range_digest');new=digest({k:remote[k] for k in ('scopeMode','coverageMode','excludedFeatureIds','abilities','exclusionNote')})
    previous_time=state.get('confirmed_range',{}).get('updatedAt')
    confirmation_changed=old!=new or (old is not None and previous_time!=remote['updatedAt'])
    if confirmation_changed and state.get('range_confirmation') and confirmation is None:
        state.setdefault('range_confirmation_history',[]).append(copy.deepcopy(state['range_confirmation']))
    state['confirmed_range']=remote;state['confirmed_range_digest']=new;state['range_ready']=True
    if old!=new:
        state['user_scenarios_confirmed']=False;state['bmk_plan_confirmed']=False;state['stage']='user_scenarios_pending'
        state.pop('pending',None)
        previous_scope=pick_fields(state,SCOPE_FIELDS)
        if previous_scope:
            state.setdefault('scope_rendering_history',[]).append({'range_digest':old,**copy.deepcopy(previous_scope)})
            for field in SCOPE_FIELDS:state.pop(field,None)
        state['outputs_stale']=True
    if confirmation is not None:state['range_confirmation']=confirmation
    elif confirmation_changed or not state.get('range_confirmation'):state['range_confirmation']={'origin':'online-confirmed','at':remote['updatedAt'],'range_digest':new,'note':'实际线上已确认记录；不得仅凭状态推断谁执行了人工审核'}
    event(state,'read_confirmed',{'range_digest':new,'remote_updated_at':remote['updatedAt']})


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cli');p.add_argument('--connection');p.add_argument('--state-root',default='work/explicit-info')
    subs=p.add_subparsers(dest='command',required=True)
    q=subs.add_parser('validate-presets');q.add_argument('--snapshot',required=True);q.add_argument('--abilities',required=True)
    q=subs.add_parser('validate-bmk');q.add_argument('--plan',required=True);q.add_argument('--scope');q.add_argument('--scenes');q.add_argument('--snapshot')
    q=subs.add_parser('prepare');q.add_argument('--snapshot',required=True);q.add_argument('--abilities',required=True);q.add_argument('--object',required=True);q.add_argument('--tool-type',required=True);q.add_argument('--round',default='initial');q.add_argument('--version',default='v1');q.add_argument('--output',required=True)
    q=subs.add_parser('create');q.add_argument('--project',required=True);q.add_argument('--snapshot',required=True);q.add_argument('--authorization',required=True)
    q=subs.add_parser('read-project');q.add_argument('project');q.add_argument('--output');q.add_argument('--snapshot',help='When local state is missing, supply the original PRD snapshot fetched from remote prdUrl');q.add_argument('--legacy-connection',help='Actual original connection record, including explicit environment, for read-only verification of unbound legacy state; never substitute a newly invented current config')
    q=subs.add_parser('confirm-default');q.add_argument('project');q.add_argument('--authorization',required=True)
    q=subs.add_parser('state');q.add_argument('project');views=q.add_mutually_exclusive_group();views.add_argument('--summary',action='store_true',help='Read compact routing context without historical events, source bodies or full proposals');views.add_argument('--section',choices=['scope','scenarios','bmk','outputs'],help='Read only the current data for one workflow section')
    q=subs.add_parser('invalidate');q.add_argument('project');q.add_argument('--from-stage',choices=['range','scenarios','bmk'],required=True);q.add_argument('--reason',required=True)
    q=subs.add_parser('set-scope');q.add_argument('project');q.add_argument('--input',required=True);q.add_argument('--presentation-only',action='store_true',help='Record a model-verified wording/evidence edit that preserves business scope, labels and feature links');q.add_argument('--reason',help='Explain why the presentation edit does not affect the confirmed scenarios or BMK')
    q=subs.add_parser('init-description');q.add_argument('--input',required=True);q.add_argument('--round',default='initial')
    q=subs.add_parser('propose');q.add_argument('project');q.add_argument('--kind',choices=['scenarios','bmk'],required=True);q.add_argument('--input',required=True)
    q=subs.add_parser('confirm');q.add_argument('project');q.add_argument('--proposal-id',required=True);q.add_argument('--authorization',required=True);q.add_argument('--origin',choices=['user','user-preauthorized-default'],default='user')
    q=subs.add_parser('outputs');q.add_argument('project');q.add_argument('--input',required=True);q.add_argument('--complete',action='store_true')
    a=p.parse_args();cmd=a.command
    if cmd=='validate-presets':out=validate_presets(read(a.snapshot),read(a.abilities))
    elif cmd=='validate-bmk':out=validate_bmk(read(a.plan),read(a.scope) if a.scope else None,read(a.scenes) if a.scenes else None,read(a.snapshot) if a.snapshot else None)
    elif cmd=='prepare':
        out=prepare(read(a.snapshot),read(a.abilities),a.object,a.tool_type,a.round,a.version);save(a.output,out);out={'ok':True,'project_key':out['projectKey'],'output':a.output}
    elif cmd=='create':
        require(nonempty(a.authorization),'Authorization evidence required')
        project=read(a.project);snapshot=read(a.snapshot)
        require(project['projectKey']==project_key(snapshot['source_document'],project['round']),'Project/source identity mismatch')
        require(project['featureCatalog']==snapshot['featureCatalog'],'Prepared project differs from source snapshot')
        validate_presets(snapshot,{'abilitiesByScope':{s:project['scopeVariants'][s]['abilities'] for s in ('p0','all')}})
        sp=state_path(a,project['projectKey']);state=read(sp) if sp.exists() else None
        if state is not None:check_state_connection(state,a,remote=True)
        existing=select_project(project['projectKey'],a)
        if existing is None:
            expected=prepare(snapshot,{'abilitiesByScope':{s:project['scopeVariants'][s]['abilities'] for s in ('p0','all')}},project['evaluationObject'],project['toolType'],project['round'],project['version'])
            require(all(project[k]==expected[k] for k in COLUMNS),'New project must preserve prepared business-function defaults')
            fields=list(COLUMNS);sql='INSERT INTO evaluation_range_state ('+', '.join(COLUMNS[k] for k in fields)+', project_name) VALUES ('+', '.join(sqlvalue(project[k],k in JSON_FIELDS) for k in fields)+', '+quote(project['evaluationObject']+' '+project['toolType'])+') ON CONFLICT (project_key) DO NOTHING;'
            execute_sql(sql,a)
        remote=select_project(project['projectKey'],a);require(remote is not None,'Project write did not produce readable row')
        if state is None:state=initial_state(project,snapshot,connection_binding(a))
        state['project_url']=url_for(project['projectKey'],a);state['latest_remote']=remote
        event(state,'project_reused' if existing else 'project_created',{'authorization':a.authorization})
        save(sp,state)
        if not (sp.parent/'source-snapshot.json').exists():save(sp.parent/'source-snapshot.json',snapshot)
        out={'ok':True,'project_key':project['projectKey'],'project_url':state['project_url'],'reused':existing is not None,'status':remote['status'],'state_file':str(sp)}
    elif cmd=='init-description':
        data=read(a.input);require(data.get('input_document_type')=='evaluation_description','Expected evaluation_description input')
        require(data.get('evaluation_scope') and data.get('scope_basis'),'Scope and basis required')
        for item in data['evaluation_scope']:
            for field in ('name','user_needs','scope_points','source_refs'):require(bool(item.get(field)),'Missing scope field: '+field)
        key=project_key(data['source_document'],a.round);sp=state_path(a,key)
        if sp.exists():out={'ok':True,'project_key':key,'reused':True,'stage':read(sp)['stage']}
        else:
            validate_scope_rendering(data['evaluation_scope'],data)
            state={**pick_fields(data,('input_document_type','source_document',*SCOPE_FIELDS)),'schema_version':1,'project_key':key,'round':a.round,'scope_origin':'source_scope','outputs':{},'events':[]}
            event(state,'description_scope_imported',{'source_id':data['source_document']['canonical_id']})
            queue_description_scope(state);save(sp,state);out={'ok':True,'project_key':key,'state_file':str(sp),'stage':state['stage'],'pending':state['pending']}
    else:
        key=extract_key(a.project,a);sp=state_path(a,key)
        if cmd=='read-project':
            state=read(sp) if sp.exists() else None
            legacy=check_state_connection(state,a,remote=True,allow_legacy=True) if state is not None else False
            remote=select_project(key,a);require(remote is not None,'No project found')
            if not sp.exists() and a.snapshot:
                snapshot=read(a.snapshot);u=urlparse(snapshot['source_document']['url']);v=urlparse(remote['prdUrl'])
                require((u.netloc,u.path)==(v.netloc,v.path),'Recovery snapshot must be fetched using remote prdUrl; do not attach another source')
                require(snapshot['featureCatalog']==remote['featureCatalog'],'Recovery facts differ from saved project; inspect original project facts without overwriting PM')
                state={'schema_version':1,'project_key':key,'source_document':snapshot['source_document'],'input_document_type':'prd','scope_origin':'pm_form','connection_binding':connection_binding(a),'project_url':url_for(key,a),'stage':'pm_pending','range_ready':False,'user_scenarios_confirmed':False,'bmk_plan_confirmed':False,'events':[],'outputs':{},'snapshot_digest':digest(snapshot)}
                event(state,'local_state_recovered',{'source_id':snapshot['source_document']['canonical_id']});save(sp,state);save(sp.parent/'source-snapshot.json',snapshot)
            if legacy:
                bind_legacy_state(state,remote,a);save(sp,state)
            elif sp.exists():
                state=read(sp);state['latest_remote']=remote
                if remote['status']=='confirmed':import_confirmed(state,remote)
                else:
                    state['range_ready']=False;state['user_scenarios_confirmed']=False;state['bmk_plan_confirmed']=False;state['stage']='pm_pending';state.pop('pending',None)
                save(sp,state)
            if a.output:save(a.output,remote)
            out={'ok':True,'project':remote,'project_url':url_for(key,a),'local_state_exists':sp.exists()}
            if legacy:out.update(legacy_binding_verified=True,local_state_update='connection_binding_only',next_action='Run read-project again to import the current remote range using the verified connection')
        elif cmd=='state':
            state=state_load(a,key)
            if a.connection and state.get('connection_binding') is not None:check_state_connection(state,a)
            out=state_view(state,sp,a.section) if a.summary or a.section else state
        elif cmd=='confirm-default':
            require(nonempty(a.authorization),'Explicit authorization to use defaults required')
            state=state_load(a,key);check_state_connection(state,a,remote=True)
            remote=select_project(key,a);require(remote is not None,'Project missing')
            require(remote['status']=='draft','Default confirmation only applies to a new draft')
            require('initial_project' in state,'Recovered project has no verifiable original default; use actual PM confirmation')
            expected=state['initial_project']
            compare=('featureCatalog','scopeMode','coverageMode','excludedFeatureIds','exclusionNote','abilities','scopeVariants')
            require(all(remote[k]==expected[k] for k in compare),'Remote differs from initial default; preserve edits and ask user about this current version')
            validate_presets({'featureCatalog':remote['featureCatalog']},{'abilitiesByScope':{s:remote['scopeVariants'][s]['abilities'] for s in ('p0','all')}})
            sql='UPDATE evaluation_range_state SET status = '+quote('confirmed')+', _updated_at = CURRENT_TIMESTAMP WHERE project_key = '+quote(key)+' AND status = '+quote('draft')+' AND _updated_at = '+quote(remote['updatedAt'])+'::timestamptz;'
            result=execute_sql(sql,a);require(isinstance(result,dict) and result.get('rows_affected')==1,'Concurrent modification or confirmation not applied')
            remote=select_project(key,a);require(remote['status']=='confirmed','Confirmation readback failed')
            state['range_confirmation']={'origin':'user-preauthorized-default','authorization':a.authorization,'at':now(),'note':'本次由用户明确授权采用默认方案，不声称PM人工double check'}
            import_confirmed(state,remote,confirmation=state['range_confirmation']);save(sp,state);out={'ok':True,'status':'confirmed','origin':state['range_confirmation']['origin'],'project_url':url_for(key,a)}
        else:
            state=state_load(a,key)
            check_state_connection(state,a)
            if cmd=='invalidate':
                require(nonempty(a.reason),'Invalidation reason required')
                state['bmk_plan_confirmed']=False
                if a.from_stage in ('range','scenarios'):state['user_scenarios_confirmed']=False
                if a.from_stage=='range':state['range_ready']=False
                state['stage']={'range':'pm_pending' if state['input_document_type']=='prd' else 'scope_pending','scenarios':'user_scenarios_pending','bmk':'bmk_pending'}[a.from_stage]
                state.pop('pending',None);state['outputs_stale']=True;event(state,'upstream_changed',{'from':a.from_stage,'reason':a.reason});out={'ok':True,'stage':state['stage']}
            elif cmd=='set-scope':
                require(state['range_ready'] or state['input_document_type']=='evaluation_description','Confirmed range required')
                restoring_description=state['input_document_type']=='evaluation_description' and not state['range_ready']
                data=read(a.input);scope=data['evaluation_scope'];require(bool(data.get('scope_basis')),'Scope basis required')
                validate_scope_rendering(scope,state)
                previous=pick_fields(state,SCOPE_FIELDS);updated={**previous,**pick_fields(data,SCOPE_FIELDS)}
                changed=previous!=updated
                pending=state.get('pending') or {}
                dependent=state.get('user_scenarios_confirmed') or state.get('bmk_plan_confirmed') or (pending and pending.get('kind')!='scope') or (state['input_document_type']=='evaluation_description' and state['range_ready'] and state.get('range_confirmation'))
                if a.presentation_only:
                    require(nonempty(a.reason),'Presentation-only edits require a reason explaining unchanged business scope')
                    require(state.get('evaluation_scope') and not restoring_description,'Presentation-only edits require an existing ready scope')
                    require(scope_structure(scope)==scope_structure(state['evaluation_scope']),'Presentation-only edits must preserve ability/need labels, order and feature links; invalidate the affected scope first')
                elif changed and state.get('evaluation_scope') and dependent:
                    require(False,'Scope changed while downstream confirmation/proposal exists; invalidate --from-stage range first (PRD: reread actual PM confirmation), or use --presentation-only --reason for a verified wording-only edit')
                state.update(pick_fields(data,SCOPE_FIELDS))
                if restoring_description or (state['input_document_type']=='evaluation_description' and changed and not a.presentation_only):
                    queue_description_scope(state)
                if changed and state.get('outputs'):
                    state['outputs_stale']=True
                    if state['stage']=='complete':state['stage']='document_pending'
                detail={'count':len(scope)}
                if a.presentation_only:detail.update(presentation_only=True,reason=a.reason)
                event(state,'scope_rendering_saved',detail);out={'ok':True,'count':len(scope),'stage':state['stage']}
                if (state.get('pending') or {}).get('kind')=='scope':out['pending']=state['pending']
            elif cmd=='propose':
                data=read(a.input)
                require(state['range_ready'],'Range is not ready')
                if a.kind=='bmk':
                    require(state['user_scenarios_confirmed'],'Confirm user scenarios first')
                    scope=state.get('evaluation_scope') or state.get('confirmed_range',{}).get('abilities')
                    validate_bmk(data,scope,state['user_scenarios'])
                else:
                    require(isinstance(data,list) and bool(data),'Scenario proposal must be a nonempty array')
                    require(all(nonempty(s.get('id')) and nonempty(s.get('name')) and nonempty(s.get('purpose')) for s in data),'Scenarios need id/name/purpose')
                    require(len({s['id'] for s in data})==len(data),'Duplicate scenario ids')
                    require(all('count' not in s for s in data),'Scene confirmation round does not allocate counts')
                    state['user_scenarios_confirmed']=False
                state['bmk_plan_confirmed']=False
                state['pending']={'kind':a.kind,'proposal_id':digest(data),'data':data,'created_at':now()};state['stage']='user_scenarios_pending' if a.kind=='scenarios' else 'bmk_pending';event(state,'proposal_created',{'kind':a.kind,'proposal_id':state['pending']['proposal_id']});out=state['pending']
            elif cmd=='confirm':
                pending=state.get('pending');require(pending and pending['proposal_id']==a.proposal_id,'Confirmation does not match current proposal')
                require(digest(pending['data'])==pending['proposal_id'],'Saved proposal content changed; present a new complete proposal')
                require(nonempty(a.authorization),'Explicit user response or recorded preauthorization required')
                which=pending['kind'];data=pending['data']
                require(which in ('scope','scenarios','bmk'),'Unknown proposal kind')
                if which=='scope':
                    require(state['input_document_type']=='evaluation_description','Scope chat confirmation applies only to description input')
                    require(digest(pick_fields(state,SCOPE_FIELDS))==pending['proposal_id'],'Scope changed; present the updated scope before confirmation')
                    state.update(range_ready=True,user_scenarios_confirmed=False,bmk_plan_confirmed=False,stage='user_scenarios_pending')
                elif which=='scenarios':
                    require(state['range_ready'],'Confirm evaluation scope first')
                    state['user_scenarios']=data;state['user_scenarios_confirmed']=True;state['bmk_plan_confirmed']=False;state['stage']='bmk_pending'
                else:
                    require(state['range_ready'] and state['user_scenarios_confirmed'],'Scope or scenario confirmation invalidated');validate_bmk(data,state.get('evaluation_scope') or state.get('confirmed_range',{}).get('abilities'),state['user_scenarios']);state['bmk_plan']=data;state['bmk_plan_confirmed']=True;state['stage']='document_pending'
                state.setdefault('confirmations',{})[which]={'proposal_id':pending['proposal_id'],'authorization':a.authorization,'origin':a.origin,'at':now()}
                if which=='scope':state['range_confirmation']={**state['confirmations'][which],'range_digest':pending['proposal_id'],'confirmed_fields':['name','user_needs'],'note':'用户在会话确认一级能力及覆盖的用户需求，不代表PM表单提交或逐项确认能力范围细节'}
                state.pop('pending',None);event(state,'proposal_confirmed',state['confirmations'][which]);out={'ok':True,'stage':state['stage']}
            elif cmd=='outputs':
                require(state.get('bmk_plan_confirmed') and state.get('user_scenarios_confirmed'),'Confirm both proposals before document output')
                outputs=read(a.input)
                for keyname in ('document_id','document_url','board_id'):
                    if keyname in state['outputs'] and keyname in outputs:require(state['outputs'][keyname]==outputs[keyname],'Default revisions must reuse existing output: '+keyname)
                state['outputs'].update(outputs)
                if a.complete:
                    require(all(state['outputs'].get(k) for k in ('document_id','document_url','board_id','readback_evidence','board_preview_evidence','showcase_quality_review')),'Completion requires actual output IDs and readback/preview/quality evidence')
                    state['stage']='complete';state['outputs_stale']=False
                else:state['stage']='document_pending'
                event(state,'outputs_recorded',{'stage':state['stage']});out={'ok':True,'stage':state['stage'],'outputs':state['outputs']}
            save(sp,state)
    print(json.dumps(out,ensure_ascii=False,indent=2))

if __name__=='__main__':
    try:main()
    except Exception as e: print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(1)
