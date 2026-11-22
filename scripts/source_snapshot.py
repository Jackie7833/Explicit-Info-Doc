#!/usr/bin/env python3
"""Preserve fetched evidence and extract model-selected atomic business facts.

The inventory is document-wide. The model must read the complete requirement-
detail section (including plain text, tables, synced blocks and nested sources)
before supplying ``--table-id`` or ``--facts``; this script only preserves and
validates source references and never decides business grouping.
"""
import argparse, hashlib, json, re, sys, xml.etree.ElementTree as ET
from pathlib import Path

DELETED = {'del', 's', 'strike'}

def text(node, active=True):
    if active and (node.tag in DELETED or 'line-through' in node.get('style', '') or node.get('strikethrough') == 'true'):
        return ''
    parts = [node.text or '']
    for child in node:
        parts.extend([text(child, active), child.tail or ''])
        if child.tag in {'p','li','br'}: parts.append(' ')
    return re.sub(r'\s+', ' ', ''.join(parts)).strip()

def parse_source(filename):
    raw = Path(filename).read_text()
    obj = json.loads(raw)
    if obj.get('ok') is not True: raise ValueError('source fetch is not successful')
    doc = obj['data']['document']
    root = ET.fromstring('<root>' + doc['content'] + '</root>')
    if not doc.get('document_id'): raise ValueError('canonical document_id missing')
    return doc, root

def table_rows(table):
    # Ignore nested tables; preserve merged cells by carrying their originating elements.
    rows=[]; pending={}
    for row in table.findall('./thead/tr') + table.findall('./tbody/tr') + table.findall('./tr'):
        out={}; next_pending={}
        for col,(cell,left) in pending.items():
            out[col]=cell
            if left>1: next_pending[col]=(cell,left-1)
        col=0
        for cell in row:
            if cell.tag not in {'td','th'}: continue
            while col in out: col+=1
            for offset in range(int(cell.get('colspan','1'))):
                out[col+offset]=cell
                if int(cell.get('rowspan','1'))>1: next_pending[col+offset]=(cell,int(cell.get('rowspan'))-1)
            col+=int(cell.get('colspan','1'))
        rows.append([out.get(i) for i in range(max(out,default=-1)+1)])
        pending=next_pending
    return rows

def block_entries(root, include_text=True, selected_ids=None, heading=None):
    selected_ids=set(selected_ids or [])
    headings=[]; blocks=[]
    for node in root.iter():
        if re.fullmatch('h[1-6]',node.tag):
            level=int(node.tag[1]); headings=[h for h in headings if h['level']<level]
            headings.append({'level':level,'id':node.get('id'),'name':text(node)})
        if not (node.get('id') and (node.tag in {'p','li','table','synced_reference','sheet','bitable'} or re.fullmatch('h[1-6]',node.tag))):
            continue
        path=[h['name'] for h in headings]
        if selected_ids and node.get('id') not in selected_ids:
            continue
        if heading and heading not in path:
            continue
        active=text(node); raw=text(node,False)
        item={'id':node.get('id'),'kind':node.tag,'heading_path':path}
        if include_text:
            item['text']=active
            if raw != active:
                item['raw_text']=raw
        else:
            item.update(char_count=len(active), has_deleted=raw != active,
                        preview=active[:160])
        blocks.append(item)
    return blocks


def inventory(doc,root,compact=False):
    headings=[]; tables=[]; references=[]; blocks=[]
    for node in root.iter():
        if re.fullmatch('h[1-6]',node.tag): headings.append({'id':node.get('id'),'name':text(node)})
        if node.tag=='table':
            rows=table_rows(node)
            tables.append({'id':node.get('id'),'rows':len(rows),'head':[text(c) if c is not None else '' for c in (rows[0] if rows else [])]})
        if 'synced' in node.tag or node.tag in {'sheet','bitable'}:
            references.append({'kind':node.tag,'attributes':node.attrib,'content':text(node)})
    # Index all content in document order. The model locates requirements by meaning;
    # no fixed chapter number or business keyword determines the scope. Compact mode
    # keeps only metadata and a short preview; full text is fetched explicitly by id.
    blocks=block_entries(root, include_text=not compact)
    if compact:
        references=[{'kind':item['kind'],'attributes':item['attributes'],
                     'char_count':len(item.get('content','')),
                     'preview':item.get('content','')[:160]} for item in references]
    return {'canonical_id':doc['document_id'],'revision':doc.get('revision_id'),'headings':headings,'tables':tables,'embedded_references':references,'content_blocks':blocks}


def selected_blocks(doc,root,block_ids=None,heading=None):
    blocks=block_entries(root, include_text=True, selected_ids=block_ids, heading=heading)
    if not blocks:
        raise ValueError('No matching content blocks')
    return {'canonical_id':doc['document_id'],'revision':doc.get('revision_id'),'content_blocks':blocks}

def apply_assessment(snapshot,assessments):
    entries=assessments.get('features',[])
    by_id={e.get('id'):e for e in entries}
    ids={f['id'] for f in snapshot['featureCatalog']}
    if len(by_id)!=len(entries) or not set(by_id)<=ids:raise ValueError('Accepted business-function assessments must use unique actual candidate IDs')
    accepted=[]
    for feature in snapshot['featureCatalog']:
        if feature['id'] not in by_id:continue
        decision=by_id[feature['id']]
        kind=decision.get('evaluationClass');reason=decision.get('evaluationReason')
        if kind!='business' or not isinstance(reason,str) or not reason.strip():
            raise ValueError('Only model-accepted real business functions may enter the final catalog')
        feature.update(evaluationClass='business',evaluationReason=reason,defaultSelected=True);accepted.append(feature)
    snapshot['featureCatalog']=accepted
    snapshot['counts']={'all':len(accepted),'p0':sum(f['priority']=='P0' for f in accepted)}
    retained={f['id'] for f in accepted}
    accepted_refs=[f.get('sourceRef',{}) for f in accepted]
    # Final structured output contains accepted business facts only. Raw source
    # evidence remains available without a second rejected/support catalog.
    for key in ('requirementCatalog','supportingRequirements','requirementToFeatureMapping','excludedRows'):
        snapshot.pop(key,None)
    snapshot['warnings']=[w for w in snapshot.get('warnings',[]) if not isinstance(w,dict) or not w.get('sourceRef') or w['sourceRef'] in accepted_refs]
    audit=snapshot.get('extractionAudit',[])
    if audit and all(row.get('table_id') for row in audit):
        for row in audit:
            row['valid_features']=sum(ref.get('table_id')==row['table_id'] and (not row.get('document_id') or ref.get('document_id')==row['document_id']) for ref in accepted_refs)
    else:
        snapshot['extractionAudit']=[{'valid_features':len(accepted),'note':'Model-selected business functions checked against complete source evidence; no non-business feature records retained.'}]
    review=snapshot.get('extractionReview',{})
    if review:
        review['issues']=[i for i in review.get('issues',[]) if i.get('provisional_feature_id') in retained]
        issue_ids={i['id'] for i in review['issues']}
        review['decisions']=[d for d in review.get('decisions',[]) if d.get('issue_id') in issue_ids]
    snapshot['evaluationReview']={'origin':'model','complete':True,'business':len(accepted)}
    return snapshot

def extract(doc, root, table_ids, source_url, color_priority=None, columns=None):
    catalog=[]; excluded=[]; audit=[]; warnings=[]; review_issues=[]
    known={n.get('id'):n for n in root.iter('table')}
    for table_id in table_ids:
        if table_id not in known: raise ValueError('table missing: '+table_id)
        table=known[table_id]; rows=table_rows(table)
        aliases={'feature':r'^(功能|功能点|功能名称|原子能力|能力名称|Function|Feature)$', 'module':r'^(模块|功能模块|分类|Module)$', 'schedule':r'^(排期|优先级|优先级/排期|Priority)$','overview':r'^(概述|功能说明|说明|功能描述|Description)$'}
        found=None
        for row_index,row in enumerate(rows):
            header=[text(c) if c is not None else '' for c in row]
            idx={k:next((i for i,s in enumerate(header) if re.match(p,s,re.I)),None) for k,p in aliases.items()}
            if columns: idx.update(columns)
            if idx['feature'] is not None: found=(row_index,header,idx);break
        if found is None: raise ValueError('No unambiguous feature header: '+table_id+'; provide --columns after inspecting the table')
        start,header,idx=found; valid=removed=blank=0
        for row_index,row in enumerate(rows[start+1:],start+1):
            def cell(k):
                i=idx.get(k)
                return row[i] if i is not None and i<len(row) else None
            feature=cell('feature')
            if feature is None or not text(feature,False): blank+=1;continue
            # Deletion applies to the whole active cell, not just its first paragraph.
            # A struck-out old label can be followed by its valid replacement.
            parts=[]
            for child in feature:
                if child.tag=='p' and text(child): parts.append(child)
                elif child.tag in {'ul','ol'}:
                    parts.extend(n for n in child.findall('./li') if text(n))
            name_node=parts[0] if parts else feature
            raw_name=text(name_node,False); name=text(name_node)
            ref={'document_id':doc['document_id'],'table_id':table_id,'row':row_index+1,'block_id':name_node.get('id')}
            if not text(feature):
                excluded.append({'rawName':raw_name,'reason':'功能名称全部为删除线内容','sourceRef':ref});removed+=1;continue
            name=re.sub(r'^\s*\d+\s*[｜|.、:：]\s*','',name)
            name=re.sub(r'^\s*[/／|｜]+\s*','',name)
            schedule=text(cell('schedule')) if cell('schedule') is not None else ''
            schedule_marks=set(re.findall(r'(?<![A-Za-z0-9])P[0-9](?![A-Za-z0-9])',schedule.upper()))
            feature_marks=set(re.findall(r'(?<![A-Za-z0-9])P[0-9](?![A-Za-z0-9])',name.upper()))
            marks=schedule_marks or feature_marks
            if schedule_marks and feature_marks and schedule_marks!=feature_marks:raise ValueError('Feature/schedule priority conflict: '+str(ref))
            evidence=[]
            if len(marks)>1: raise ValueError('Conflicting priority markers: '+str(ref))
            priority=next(iter(marks),'未标注')
            if marks:evidence.append(('排期/优先级单元格原文：'+schedule) if schedule_marks else ('功能名称原文显式标记：'+name))
            colors=sorted({c.get('background-color') for c in row if c is not None and c.get('background-color')})
            if color_priority:
                color_marks={color_priority[c] for c in colors if c in color_priority}
                if color_marks and marks and color_marks!=marks: raise ValueError('Color/text priority conflict: '+str(ref))
                if not marks and len(color_marks)==1:
                    priority=next(iter(color_marks)); evidence.append('已由原文图例或用户明确颜色含义：'+','.join(colors))
                elif len(color_marks)>1:raise ValueError('Multiple priority colors: '+str(ref))
            if not marks and schedule and priority=='未标注': priority='非P0'
            if not marks and colors and not color_priority: warnings.append({'sourceRef':ref,'notice':'颜色保留为证据，未将高亮自动认定为P0','colors':colors})
            module=cell('module'); module_ps=[p for p in module.findall('./p') if text(p)] if module is not None else []
            fid='f-'+hashlib.sha256((doc['document_id']+'|'+table_id+'|'+str(row_index+1)).encode()).hexdigest()[:16]
            if len(parts)>1:
                issue_id='cell-'+hashlib.sha256((doc['document_id']+'|'+table_id+'|'+str(row_index+1)).encode()).hexdigest()[:16]
                review_issues.append({'id':issue_id,'kind':'multiple_content_parts','sourceRef':ref,'provisional_feature_id':fid,'candidates':[{'block_id':n.get('id'),'text':text(n)} for n in parts]})
                warnings.append({'sourceRef':ref,'notice':'功能格含多个非空段落/列表项；必须由模型判断是功能+备注还是多个真实功能，不可把首段当作已完成全量抽取。','review_issue_id':issue_id})
            catalog.append({'id':fid,'name':name,'priority':priority,'sourceModule':text(module_ps[0]) if module_ps else (text(module) if module is not None else ''),'sourceOverview':text(cell('overview')) if cell('overview') is not None else '', 'schedule':schedule,'priorityEvidence':evidence,'sourceRef':ref,'sourceCells':dict(zip(header,[text(c) if c is not None else '' for c in row])),'rawFeatureText':text(feature,False),'activeFeatureText':text(feature),'colors':colors})
            valid+=1
        audit.append({'document_id':doc['document_id'],'table_id':table_id,'data_rows':len(rows)-start-1,'valid_features':valid,'deleted_features':removed,'blank_feature_rows':blank})
    if not catalog: raise ValueError('No valid features; do not create an empty project')
    title=next((text(n) for n in root.iter('title')),'')
    return {'schema_version':1,'source_document':{'canonical_id':doc['document_id'],'title':title,'url':source_url,'revision':doc.get('revision_id')},'featureCatalog':catalog,'excludedRows':excluded,'extractionAudit':audit,'warnings':warnings,'extractionReview':{'issues':review_issues,'decisions':[]},'evidence':{'xml':doc['content'],'reference_map':doc.get('reference_map',{}),'sha256':hashlib.sha256(doc['content'].encode()).hexdigest()},'counts':{'all':len(catalog),'p0':sum(f['priority']=='P0' for f in catalog)}}

def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('fetch_json');p.add_argument('--inventory',action='store_true');p.add_argument('--compact-inventory',action='store_true',help='Return metadata and short previews without full block text');p.add_argument('--blocks',action='append',help='Return selected block text by id; repeatable');p.add_argument('--heading',help='Return block text under a matching heading name');p.add_argument('--table-id',action='append');p.add_argument('--url',default='');p.add_argument('--output');p.add_argument('--color-priority',help='JSON map; only supply when supported by a source legend or explicit user statement');p.add_argument('--review',help='Model-authored review decisions JSON {decisions:[{issue_id,decision,rationale,feature_ids}]} for multi-part feature cells');p.add_argument('--selection',help='JSON list of {fetch_json,table_ids,source_url?,columns?,color_priority?} for model-selected tables including expanded synchronized sources');p.add_argument('--facts',help='Model-extracted atomic business facts from the entire requirements detail (plain text/lists/unusual layouts), with sourceRef block IDs; script validates evidence references only');p.add_argument('--columns',help='JSON map {feature:0,schedule:1,module:...}, zero based; for unusual headers')
    p.add_argument('--assessment',help='Model-selected true business functions only: {features:[{id,evaluationClass:business,evaluationReason}]}; omitted candidates are removed, never emitted as unchecked engineering items')
    p.add_argument('--evidence-source',action='append',default=[],help='Additional successful source-fetch JSON used by --facts for expanded synced text/mixed layouts; main source identity stays unchanged')
    a=p.parse_args();doc,root=parse_source(a.fetch_json)
    if a.blocks or a.heading:
        out=selected_blocks(doc,root,a.blocks,a.heading)
    elif a.inventory: out=inventory(doc,root,compact=a.compact_inventory)
    elif a.selection:
        selections=json.loads(Path(a.selection).read_text());parts=[];documents=[]
        for item in selections:
            sd,sr=parse_source(item['fetch_json']);part=extract(sd,sr,item['table_ids'],item.get('source_url',''),item.get('color_priority'),item.get('columns'));parts.append(part)
            documents.append({'document_id':sd['document_id'],'revision':sd.get('revision_id'),'xml':sd['content'],'reference_map':sd.get('reference_map',{})})
        if not parts:raise ValueError('Empty source selection')
        out={'schema_version':1,'source_document':{'canonical_id':doc['document_id'],'title':next((text(n) for n in root.iter('title')),''),'url':a.url,'revision':doc.get('revision_id')},'featureCatalog':sum((x['featureCatalog'] for x in parts),[]),'excludedRows':sum((x['excludedRows'] for x in parts),[]),'extractionAudit':sum((x['extractionAudit'] for x in parts),[]),'warnings':sum((x['warnings'] for x in parts),[]),'extractionReview':{'issues':sum((x['extractionReview']['issues'] for x in parts),[]),'decisions':[]},'evidence':{'xml':doc['content'],'reference_map':doc.get('reference_map',{}),'sha256':hashlib.sha256(doc['content'].encode()).hexdigest(),'documents':documents}}
        if len({f['id'] for f in out['featureCatalog']})!=len(out['featureCatalog']):raise ValueError('Same source row selected multiple times')
        out['counts']={'all':len(out['featureCatalog']),'p0':sum(f['priority']=='P0' for f in out['featureCatalog'])}
    elif a.facts:
        facts=json.loads(Path(a.facts).read_text());features=facts['featureCatalog'];nodes={n.get('id'):n for n in root.iter() if n.get('id')}
        evidence_nodes={doc['document_id']:nodes};extra_documents=[]
        for filename in a.evidence_source:
            sd,sr=parse_source(filename)
            if sd['document_id'] in evidence_nodes:raise ValueError('Duplicate evidence source: '+sd['document_id'])
            evidence_nodes[sd['document_id']]={n.get('id'):n for n in sr.iter() if n.get('id')}
            extra_documents.append({'document_id':sd['document_id'],'revision':sd.get('revision_id'),'xml':sd['content'],'reference_map':sd.get('reference_map',{})})
        for f in features:
            ref=f.get('sourceRef',{})
            referenced_nodes=evidence_nodes.get(ref.get('document_id'),{})
            if ref.get('block_id') not in referenced_nodes:raise ValueError('Unresolved text fact sourceRef: '+str(ref))
            for additional_ref in [*f.get('supportingSourceRefs',[]),*f.get('prioritySourceRefs',[])]:
                if additional_ref.get('block_id') not in evidence_nodes.get(additional_ref.get('document_id'),{}):raise ValueError('Unresolved business-condition evidence: '+str(additional_ref))
            f['sourceEvidenceXML']=ET.tostring(referenced_nodes[ref['block_id']],encoding='unicode')
            if not f.get('id'):f['id']='f-'+hashlib.sha256((ref['document_id']+'|'+ref['block_id']+'|'+f['name']).encode()).hexdigest()[:16]
        if not features:raise ValueError('No model-extracted facts')
        out={'schema_version':1,'source_document':{'canonical_id':doc['document_id'],'title':next((text(n) for n in root.iter('title')),''),'url':a.url,'revision':doc.get('revision_id')},'featureCatalog':features,'excludedRows':facts.get('excludedRows',[]),'extractionAudit':facts.get('extractionAudit',[]),'warnings':['Plain text facts are model-extracted; source presence is checked, semantic completeness requires model review.'],'extractionMethod':'model_facts','extractionReview':facts.get('extractionReview',{'issues':[],'decisions':[]}),'evidence':{'xml':doc['content'],'reference_map':doc.get('reference_map',{}),'sha256':hashlib.sha256(doc['content'].encode()).hexdigest()},'counts':{'all':len(features),'p0':sum(f['priority']=='P0' for f in features)}}
        if extra_documents:out['evidence']['documents']=extra_documents
        if 'sourceSemanticReview' in facts:out['sourceSemanticReview']=facts['sourceSemanticReview']
    elif a.table_id:out=extract(doc,root,a.table_id,a.url,json.loads(a.color_priority) if a.color_priority else None,json.loads(a.columns) if a.columns else None)
    else:p.error('Inspect the full requirement-detail inventory and selected blocks, then provide model-selected --table-id or --facts; never guess feature scope from a chapter number')
    if a.review:
        decisions=json.loads(Path(a.review).read_text())
        out.setdefault('extractionReview',{'issues':[]})['decisions']=decisions['decisions']
    if a.assessment:
        if a.inventory:raise ValueError('--assessment requires extracted features, not an inventory')
        apply_assessment(out,json.loads(Path(a.assessment).read_text()))
    serialized=json.dumps(out,ensure_ascii=False,indent=2)
    if a.output:Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(serialized+'\n')
    else:print(serialized)
if __name__=='__main__':
    try:main()
    except Exception as e: print(json.dumps({'ok':False,'error':str(e)},ensure_ascii=False),file=sys.stderr);sys.exit(1)
