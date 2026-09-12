from __future__ import annotations
import io
from PIL import Image, ImageDraw, ImageFont, ImageFilter
W,H=900,1200
BG=(2,8,16,255); WHITE=(242,248,255); MUTED=(155,185,210); GREEN=(65,255,145); CYAN=(35,205,255); GOLD=(255,205,70); RED=(255,75,90); PURPLE=(190,105,255); ORANGE=(255,165,55)
def font(n,b=False):
 p='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'; return ImageFont.truetype(p,n)
def glow(im,xy,radius,rgb,alpha=90):
 lay=Image.new('RGBA',im.size,(0,0,0,0)); d=ImageDraw.Draw(lay); x,y=xy
 for r in range(radius,8,-10): d.ellipse((x-r,y-r,x+r,y+r),fill=(*rgb,max(0,int(alpha*(1-r/radius)**1.5))))
 im.alpha_composite(lay.filter(ImageFilter.GaussianBlur(16)))
def tx(d,xy,text,size,fill=WHITE,b=False,anchor='la'): d.text(xy,str(text),font=font(size,b),fill=fill,anchor=anchor)
def panel(d,box,title,accent):
 d.rounded_rectangle(box,22,fill=(5,18,31,245),outline=(*accent,220),width=3); x1,y1,x2,_=box; d.rounded_rectangle((x1+2,y1+2,x2-2,y1+62),20,fill=(*accent,28),outline=(*accent,80),width=1); tx(d,(x1+24,y1+31),title,24,accent,True,'lm')
def kv(d,x,y,label,value,color=WHITE): tx(d,(x,y),label,21,MUTED,False,'lm'); tx(d,(x+250,y),value,21,color,True,'lm')
def fmt(sec):
 sec=max(0,int(sec)); return f'{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}' if sec>=3600 else f'{sec//60:02d}:{sec%60:02d}'
def bar(d,x1,y,x2,ratio,color):
 d.rounded_rectangle((x1,y,x2,y+18),9,fill=(18,30,45),outline=(55,85,110),width=1); d.rounded_rectangle((x1,y,x1+int((x2-x1)*max(0,min(1,ratio))),y+18),9,fill=color)
def card(kind='selected',asset='ASIA_X',direction='',confidence=0,expiry=0,reason='',**kw):
 im=Image.new('RGBA',(W,H),BG); glow(im,(120,160),260,(0,110,255),70); glow(im,(790,870),300,(0,230,140),75); glow(im,(450,1150),260,(255,180,30),45); d=ImageDraw.Draw(im,'RGBA')
 d.rounded_rectangle((14,14,W-14,H-14),30,fill=(3,12,24,248),outline=(*CYAN,210),width=4)
 d.rounded_rectangle((30,30,W-30,176),26,fill=(5,16,29,250),outline=(*GOLD,190),width=3); d.ellipse((54,54,148,148),fill=(5,27,45),outline=(*GOLD,220),width=4); d.arc((68,68,134,134),25,330,fill=GOLD,width=8); d.line((99,111,125,88),fill=GREEN,width=7); d.polygon([(125,88),(114,90),(124,101)],fill=GREEN)
 tx(d,(170,78),'CANDICE',48,WHITE,True,'lm'); tx(d,(171,119),'AI TRADING BOT',22,GOLD,True,'lm'); tx(d,(W-58,64),'LIVE MARKET',18,GREEN,True,'ra'); tx(d,(W-58,96),'FLEX / FIXED-TIME',17,CYAN,True,'ra'); tx(d,(W-58,129),'MANUAL ONLY',17,WHITE,True,'ra'); d.line((170,150,W-55,150),fill=(*CYAN,120),width=2)
 titles={'selected':('ASSET SELECTED',GREEN),'signal':('AI SIGNAL',CYAN),'result':('TRADE RESULT',PURPLE),'session':('SESSION TIME',ORANGE),'expiry':('EXPIRY REACHED',RED),'recovery':('RECOVERY TIME',GREEN),'status':('SYSTEM STATUS',CYAN)}; title,accent=titles.get(kind,('CANDICE UPDATE',CYAN)); panel(d,(30,198,W-30,1030),title,accent)
 tx(d,(60,280),asset,48,WHITE,True,'lm'); d.rounded_rectangle((650,250,840,298),20,fill=(35,25,4),outline=GOLD,width=2); tx(d,(745,274),'OTC • FLEX',18,GOLD,True,'mm')
 if kind=='selected':
  rows=[('✓','Fresh 1-minute candle','VERIFIED',GREEN),('◎','Candice AI analysis','ON',CYAN),('◷','Next signal window','NEXT 5 MIN',WHITE),('⏱','AI duration','2 / 3 / 5 / 10 / 15 MIN',WHITE),('◉','Selection','CONFIRMED',GREEN)]; y=350
  for icon,label,value,col in rows: tx(d,(70,y),icon,27,col,True,'lm'); tx(d,(125,y),label,21,MUTED,False,'lm'); tx(d,(810,y),value,21,col,True,'ra'); d.line((65,y+28,835,y+28),fill=(35,75,105,90),width=1); y+=88
  tx(d,(450,830),'ASSET READY FOR CANDICE RESEARCH',24,GREEN,True,'mm'); tx(d,(450,875),'Research continues while this asset rests.',19,MUTED,False,'mm')
 elif kind=='signal':
  col=GREEN if direction=='UP' else RED if direction=='DOWN' else WHITE; tx(d,(60,355),direction or 'WAIT',72,col,True,'lm'); kv(d,60,455,'Entry',kw.get('entry','—')); kv(d,60,505,'Duration',f'{expiry} MIN'); kv(d,60,555,'Candice AI',f'APPROVED • {confidence}%',GREEN); kv(d,60,605,'Technical',kw.get('technical','PASSED'),CYAN); kv(d,60,655,'Trend',kw.get('trend',direction or '—'),col)
  total=max(1,int(kw.get('total_seconds',expiry*60))); remain=max(0,int(kw.get('remaining',total))); ratio=remain/total; tx(d,(60,720),'TIME REMAINING',20,GOLD,True,'lm'); tx(d,(450,770),fmt(remain),54,WHITE,True,'mm'); bar(d,60,805,840,ratio,col); tx(d,(450,850),f'EXPIRY • {kw.get("expiry_time","VERIFYING")}',23,ORANGE,True,'mm'); tx(d,(450,900),(reason or 'Fresh aligned market evidence.')[:85],17,WHITE,False,'mm')
 elif kind=='expiry':
  tx(d,(450,370),'EXPIRY REACHED',50,RED,True,'mm'); kv(d,60,470,'Direction',direction or '—',RED if direction=='DOWN' else GREEN); kv(d,60,525,'Entry',kw.get('entry','—')); kv(d,60,580,'Duration',f'{expiry} MIN'); kv(d,60,635,'Expiry Boundary',kw.get('expiry_time','—'),ORANGE); tx(d,(450,760),'VERIFYING CLOSED EXPIRY CANDLE…',25,ORANGE,True,'mm'); tx(d,(450,810),'Please wait for verified WIN / LOSS.',20,MUTED,False,'mm')
 elif kind=='result':
  result=direction or 'UNRESOLVED'; col=GREEN if result=='WIN' else RED if result=='LOSS' else ORANGE; tx(d,(450,370),result,76,col,True,'mm'); kv(d,70,475,'Entry',kw.get('entry','—')); kv(d,70,525,'Expiry',kw.get('exit','—')); kv(d,70,575,'Duration',f'{expiry} MIN'); kv(d,70,625,'Verification',kw.get('verification','candle-closed'),GREEN); kv(d,70,675,'Result Time',kw.get('result_time','—'),CYAN); tx(d,(450,795),'MARKET OUTCOME • NOT BROKER ACCOUNT P/L',20,MUTED,True,'mm')
 elif kind=='session':
  active=kw.get('active',False); remain=max(0,int(kw.get('remaining',0))); total=max(1,int(kw.get('total_seconds',1))); kv(d,60,380,'Session Start',kw.get('session_start','—'),GREEN); kv(d,60,450,'Session End',kw.get('session_end','—'),RED); kv(d,60,520,'Next Session',kw.get('next_session','—'),CYAN); kv(d,60,590,'Time Remaining',fmt(remain),GREEN); bar(d,60,635,840,remain/total,GREEN if active else ORANGE); tx(d,(450,720),'SIGNAL SESSION ACTIVE' if active else 'RESEARCH ONLY',32,GREEN if active else ORANGE,True,'mm')
 elif kind=='recovery':
  kv(d,60,390,'Recovery Window',kw.get('recovery','15 MIN'),GREEN); kv(d,60,460,'Quick Recovery',kw.get('quick_recovery','5 MIN'),ORANGE); kv(d,60,530,'Recovery Countdown',fmt(kw.get('remaining',300)),CYAN); bar(d,60,585,840,kw.get('ratio',1),CYAN); kv(d,60,660,'Next Signal',kw.get('next_signal','Automatic'),CYAN); kv(d,60,720,'Daily Loss Limit',kw.get('daily_limit','ACTIVE'),GREEN); tx(d,(450,800),'RECOVERY PROTECTION ACTIVE',30,GREEN,True,'mm')
 else:
  tx(d,(450,390),'CANDICE AI',52,GREEN,True,'mm'); tx(d,(450,470),'LIVE MARKET • AI ANALYSIS',25,CYAN,True,'mm'); tx(d,(450,560),kw.get('status_text','System online.'),22,WHITE,False,'mm')
 d.rounded_rectangle((30,900,W-30,970),18,fill=(7,15,25),outline=(*RED,120),width=2); tx(d,(115,935),'FOREX OFF',20,RED,True,'mm'); tx(d,(310,935),'AUTO-TRADE OFF',20,RED,True,'mm'); tx(d,(525,935),'MARTINGALE OFF',20,RED,True,'mm'); tx(d,(760,935),'MANUAL ONLY',20,GREEN,True,'mm')
 d.rounded_rectangle((30,1050,W-30,1170),25,fill=(5,20,29),outline=(*GOLD,190),width=3); tx(d,(450,1080),'CANDICE AI',34,GOLD,True,'mm'); tx(d,(450,1118),'TRADE SMARTER WITH CANDICE',21,WHITE,True,'mm'); tx(d,(450,1148),'OBSERVE • ANALYZE • COMPARE • LEARN • DECIDE • MONITOR • IMPROVE',14,GREEN,True,'mm')
 return im.convert('RGB')
def png_bytes(**kwargs):
 b=io.BytesIO(); card(**kwargs).save(b,format='PNG',optimize=True); b.seek(0); b.name='candice-update.png'; return b
def gif_bytes(**kwargs): return png_bytes(**kwargs)
def update_gif_bytes(): return png_bytes(kind='status',asset='SYSTEM',status_text='CANDICE visual model active')