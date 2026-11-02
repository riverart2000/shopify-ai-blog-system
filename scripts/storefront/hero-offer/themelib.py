import json, re, importlib.util
spec=importlib.util.spec_from_file_location("shp","shp.py"); shp=importlib.util.module_from_spec(spec); spec.loader.exec_module(shp)
DUP="gid://shopify/OnlineStoreTheme/210264916313"
def get_file(name, theme=DUP):
    r=shp.gql('query($f:[String!]){ theme(id:"%s"){ files(filenames:$f, first:5){ nodes{ filename body{ ... on OnlineStoreThemeFileBodyText{content} } } } } }'%theme,{"f":[name]})
    n=r['data']['theme']['files']['nodes']; return n[0]['body']['content'] if n else None
def get_json(name, theme=DUP):
    s=get_file(name,theme); return json.loads(re.sub(r'^\s*/\*.*?\*/\s*','',s,flags=re.S))
def put(name, content, theme=DUP):
    if not isinstance(content,str): content=json.dumps(content,indent=2,ensure_ascii=False)
    r=shp.gql('mutation($t:ID!,$f:[OnlineStoreThemeFilesUpsertFileInput!]!){ themeFilesUpsert(themeId:$t, files:$f){ upsertedThemeFiles{filename} userErrors{filename code message} } }',{"t":theme,"f":[{"filename":name,"body":{"type":"TEXT","value":content}}]})
    d=r.get('data',{}).get('themeFilesUpsert') or r; 
    if not d.get('upsertedThemeFiles'): raise SystemExit('PUT FAILED '+name+' '+json.dumps(d)[:600])
    return d
def text_block(html, preset="rte", align="left", width="100%", font="var(--font-body--family)", case="none", spacing="normal", size=""):
    return {"type":"text","settings":{"text":html,"width":width,"max_width":"normal","alignment":align,"type_preset":preset,"font":font,"font_size":size,"line_height":"normal" if preset!="rte" else "relaxed","letter_spacing":spacing,"case":case,"wrap":"pretty","text_color":"","background":False,"background_color":"#00000026","corner_radius":0,"padding-block-start":0,"padding-block-end":0,"padding-inline-start":0,"padding-inline-end":0},"blocks":{}}
def eyebrow(txt): return text_block(f"<p>{txt}</p>",preset="h5",font="var(--font-primary--family)",case="uppercase",spacing="wide")
def heading(txt,level="h2"): return text_block(f"<{level}>{txt}</{level}>",preset=level,font="var(--font-primary--family)",width="fit-content")
def button(label,link,style="button"):
    return {"type":"button","settings":{"label":label,"link":link,"open_in_new_tab":False,"style_class":style},"blocks":{}}
def group_row(blocks):
    return {"type":"group","settings":{"content_direction":"row","vertical_on_mobile":False,"horizontal_alignment":"flex-start","vertical_alignment":"center","align_baseline":False,"horizontal_alignment_flex_direction_column":"flex-start","vertical_alignment_flex_direction_column":"center","gap":12,"width":"fill","custom_width":100,"width_mobile":"fill","custom_width_mobile":100,"height":"fit","custom_height":100,"background_media":"none","background_color":"","video_position":"cover","background_image_position":"cover","border":"none","border_width":1,"border_opacity":100,"border_radius":0,"padding-block-start":0,"padding-block-end":0,"padding-inline-start":0,"padding-inline-end":0},"blocks":blocks,"block_order":list(blocks.keys())}
def media_with_content(image, content_blocks, position="left", bg=""):
    return {"type":"media-with-content","blocks":{
        "media":{"type":"_media-without-appearance","static":True,"settings":{"media_type":"image","image":image,"link":"","video_loop":True,"video_autoplay":False,"image_position":"cover","video_position":"cover"},"blocks":{}},
        "content":{"type":"_content-without-appearance","static":True,"settings":{"horizontal_alignment_flex_direction_column":"flex-start","vertical_alignment_flex_direction_column":"center","gap":20},"blocks":content_blocks,"block_order":list(content_blocks.keys())}},
        "settings":{"media_position":position,"media_width":"50","media_height":"medium","section_width":"page-width","extend_media":False,"background_color":bg,"padding-block-start":48,"padding-block-end":48}}
def video_slot_eyebrow(vid): return eyebrow(f"Founder video slot {vid} · placeholder until recorded")
