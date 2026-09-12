from __future__ import annotations
import io
from PIL import Image, ImageDraw, ImageFont, ImageFilter
W,H=1024,1280
WHITE=(242,248,255); MUTED=(145,175,205); GREEN=(55,255,145); CYAN=(30,205,255); GOLD=(255,205,65); RED=(255,70,85); PURPLE=(190,105,255); ORANGE=(255,160,55); BG=(2,8,16)
def font(n,b=False): return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',n)
def tx(d,xy,s,n,fill=WHITE,b=False,anchor='la'): d.text(xy,str(s),font=font(n,b),fill=fill,anchor=anchor)
def glow(im,xy,rgb,r=180,a=80):
 lay=Image.new('RGBA',im.size,(0,0,0,0)); d=ImageDraw.Draw(lay); x,y=xy
 for q in range(r,10,-12): d.ellipse((x-q,y-q,x+q,y+q),fill=(*rgb,max(0,int(a*(1-q/r)**1.4))))
 im.alpha_composite(lay.filter(ImageFilter.GaussianBlur(22)))
def panel(d,box,title,accent):
 d.rounded_rectangle(box,30,fill=(5,17,30,242),outline=(*accent,220),width=4); x1,y1,x2,_=box; d.rounded_rectangle((x1+2,y1+2,x2-2,y1+70),28,fill=(*accent,22)); tx(d,(x1+30,y1+35),title,30,accent,True,'lm')
def kv(d,y,label,value,color=WHITE): tx(d,(90,y),label,27,MUTED,False,'lm'); tx(d,(850,y),value,27,color,True,'rm')
def fmt(sec):
 sec=max(0,int(sec)); return f'{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}' if sec>=3600 else f'{sec//60:02d}:{sec%60:02d}'
def bar(d,x1,y,x2,ratio,color):
 d.rounded_rectangle((x1,y,x2,y+18),9,fill=(18,30,45),outline=(55,85,110),width=1); d.rounded_rectangle((x1,y,x1+int((x2-x1)*max(0,min(1,ratio))),y+18),9,fill=color)
def progress(d,cx,cy,r,remaining,total,accent):
 d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=(25,55,80),width=14); frac=max(0,min(1,remaining/max(1,total))); d.arc((cx-r,cy-r,cx+r,cy+r),-90,-90+360*frac,fill=accent,width=14); tx(d,(cx,cy-8),fmt(remaining),68,WHITE,True,'mm'); tx(d,(cx,cy+65),'TIME REMAINING',23,MUTED,True,'mm')
def card(kind='selected',asset='ASIA_X',direction='',confidence=0,expiry=0,reason='',**kw):
 im=Image.new('RGBA',(W,H),BG); glow(im,(120,160),(0,110,255),260,70); glow(im,(850,900),(0,230,140),300,70); glow(im,(500,1150),(255,180,30),260,40); d=ImageDraw.Draw(im,'RGBA')
 d.rounded_rectangle((24,24,W-24,H-24),44,fill=(3,11,22,248),outline=(*CYAN,220),width=4)
 d.rounded_rectangle((48,48,W-48,190),30,fill=(5,16,29,250),outline=(*GOLD,210),width=3); d.ellipse((70,70,160,160),fill=(4,25,42),outline=(*GOLD,220),width=4); d.arc((85,85,145,145),25,330,fill=GOLD,width=7); d.line((115,125,143,99),fill=GREEN,width=6)
 tx(d,(180,100),'CANDICE',50,WHITE,True,'lm'); tx(d,(181,145),'AI TRADING BOT',23,GOLD,True,'lm'); tx(d,(970,90),'LIVE FLEX',21,GREEN,True,'ra'); tx(d,(970,125),'FIXED-TIME',19,CYAN,True,'ra'); tx(d,(970,158),'MANUAL ONLY',18,WHITE,True,'ra')
 titles={'selected':('ASSET SELECTED',GREEN),'signal':('AI SIGNAL',CYAN),'result':('TRADE RESULT',PURPLE),'session':('SESSION TIME',ORANGE),'expiry':('EXPIRY REACHED',RED),'recovery':('RECOVERY TIME',GREEN),'status':('SYSTEM STATUS',CYAN)}; title,accent=titles.get(kind,('CANDICE UPDATE',CYAN)); panel(d,(48,215,W-48,1060),title,accent)
 tx(d,(80,300),asset,52,WHITE,True,'lm'); d.rounded_rectangle((735,270,945,320),22,fill=(35,25,4),outline=GOLD,width=2); tx(d,(840,295),'OTC • FLEX',19,GOLD,True,'mm')
 if kind=='selected':
  kv(d,385,'Fresh 1-minute candle',kw.get('candle','VERIFIED'),GREEN); kv(d,455,'Asset select time',kw.get('selected_time','—'),CYAN); kv(d,525,'Candice AI analysis','ON',GREEN); kv(d,595,'Research','24/7',CYAN); kv(d,665,'Signal window','NEXT 5 MIN',WHITE); kv(d,735,'AI duration','2 / 3 / 5 / 10 / 15 MIN',WHITE); tx(d,(512,850),'ASSET READY',34,GREEN,True,'mm'); tx(d,(512,900),'CANDICE continues live research.',22,MUTED,False,'mm')
 elif kind=='signal':
  col=GREEN if direction=='UP' else RED if direction=='DOWN' else WHITE; tx(d,(512,370),direction or 'WAIT',78,col,True,'mm'); total=max(1,int(kw.get('total_seconds',expiry*60))); rem=max(0,int(kw.get('remaining',total))); progress(d,512,570,150,rem,total,accent); kv(d,790,'Signal time',kw.get('signal_time','—'),CYAN); kv(d,850,'Entry',kw.get('entry','—')); kv(d,910,'Duration',f'{expiry} MIN'); kv(d,970,'AI approval',f'{confidence}%',GREEN); tx(d,(512,1010),f'EXPIRY • {kw.get("expiry_time","VERIFYING")}',20,ORANGE,True,'mm')
 elif kind=='expiry':
  tx(d,(512,395),'EXPIRY REACHED',48,RED,True,'mm'); kv(d,515,'Direction',direction or '—',RED if direction=='DOWN' else GREEN); kv(d,575,'Entry',kw.get('entry','—')); kv(d,635,'Duration',f'{expiry} MIN'); kv(d,695,'Expiry Boundary',kw.get('expiry_time','—'),ORANGE); kv(d,755,'Reached At',kw.get('reached_time','—'),CYAN); tx(d,(512,850),'VERIFYING EXPIRY CANDLE…',28,ORANGE,True,'mm'); tx(d,(512,900),'Please wait for verified WIN / LOSS.',22,MUTED,False,'mm')
 elif kind=='result':
  result=direction or 'UNRESOLVED'; col=GREEN if result=='WIN' else RED if result=='LOSS' else ORANGE; tx(d,(512,405),result,82,col,True,'mm'); kv(d,540,'Entry',kw.get('entry','—')); kv(d,600,'Expiry price',kw.get('exit','—')); kv(d,660,'Duration',f'{expiry} MIN'); kv(d,720,'Verification',kw.get('verification','candle-closed'),GREEN); kv(d,780,'Result Time',kw.get('result_time','—'),CYAN); tx(d,(512,875),'MARKET OUTCOME • NOT BROKER ACCOUNT P/L',20,MUTED,True,'mm')
 elif kind=='session':
  active=kw.get('active',False); rem=max(0,int(kw.get('remaining',0))); total=max(1,int(kw.get('total_seconds',10800))); kv(d,400,'Session Start',kw.get('session_start','—'),GREEN); kv(d,470,'Session End',kw.get('session_end','—'),RED); kv(d,540,'Next Session',kw.get('next_session','—'),CYAN); progress(d,512,750,125,rem,total,GREEN if active else ORANGE); tx(d,(512,930),'SIGNAL SESSION ACTIVE' if active else 'RESEARCH ONLY',30,GREEN if active else ORANGE,True,'mm')
 elif kind=='recovery':
  rem=max(0,int(kw.get('remaining',300))); total=max(1,int(kw.get('total_seconds',300))); kv(d,400,'Recovery Window',kw.get('recovery','15 MIN'),GREEN); kv(d,470,'Quick Recovery',kw.get('quick_recovery','5 MIN'),ORANGE); progress(d,512,650,125,rem,total,CYAN); kv(d,850,'Next Signal',kw.get('next_signal','After recovery'),CYAN); kv(d,910,'Daily Loss Limit',kw.get('daily_limit','ACTIVE'),GREEN); tx(d,(512,970),'RECOVERY PROTECTION ACTIVE',25,GREEN,True,'mm')
 else:
  tx(d,(512,400),'CANDICE AI',55,GREEN,True,'mm'); tx(d,(512,490),'LIVE MARKET • AI ANALYSIS',28,CYAN,True,'mm'); tx(d,(512,590),kw.get('status_text','System online.'),24,WHITE,False,'mm')
 d.rounded_rectangle((48,1080,W-48,1230),30,fill=(5,20,29),outline=(*GOLD,190),width=3); tx(d,(512,1115),'CANDICE AI',38,GOLD,True,'mm'); tx(d,(512,1160),'TRADE SMARTER WITH CANDICE',20,WHITE,True,'mm'); tx(d,(512,1198),'FLEX • MANUAL ONLY • AUTO-TRADE OFF • MARTINGALE OFF',16,GREEN,True,'mm'); return im.convert('RGB')
def png_bytes(**kwargs):
 b=io.BytesIO(); card(**kwargs).save(b,format='PNG',optimize=True); b.seek(0); b.name='candice-update.png'; return b
def gif_bytes(**kwargs): return png_bytes(**kwargs)
def update_gif_bytes(): return png_bytes(kind='status',asset='SYSTEM',status_text='CANDICE visual model active')