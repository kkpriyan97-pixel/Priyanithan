from __future__ import annotations
import io,math
from PIL import Image,ImageDraw,ImageFont,ImageFilter
W,H=1024,1280
WHITE=(245,250,255);MUTED=(145,180,210);CYAN=(0,215,255);GREEN=(45,255,145);GOLD=(255,205,65);RED=(255,70,85);ORANGE=(255,165,55);PURPLE=(190,105,255);BG=(2,8,18)
def font(n,b=False):return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',n)
def tx(d,xy,s,n,fill=WHITE,b=False,anchor='la'):d.text(xy,str(s),font=font(n,b),fill=fill,anchor=anchor)
def rr(d,b,r,fill,outline=None,width=1):d.rounded_rectangle(b,radius=r,fill=fill,outline=outline,width=width)
def glow(im,xy,c,r=180,a=90):
 l=Image.new('RGBA',im.size,(0,0,0,0));d=ImageDraw.Draw(l);x,y=xy
 for q in range(r,8,-10):d.ellipse((x-q,y-q,x+q,y+q),fill=(*c,max(0,int(a*(1-q/r)**1.6))))
 im.alpha_composite(l.filter(ImageFilter.GaussianBlur(20)))
def fmt(s):s=max(0,int(s));return f'{s//3600:02d}:{s%3600//60:02d}:{s%60:02d}' if s>=3600 else f'{s//60:02d}:{s%60:02d}'
def chart(d,b,bull=True):
 x1,y1,x2,y2=b;p=y2-25;step=(x2-x1)/12
 for i in range(11):
  x=x1+step*(i+1);y=max(y1+16,min(y2-16,p+(-1 if bull else 1)*(12+i*2)+math.sin(i*1.7)*16));c=GREEN if y<p else RED;d.line((x,y-30,x,y+30),fill=c,width=5);d.rectangle((x-9,y-14,x+9,y+14),fill=c);p=y
def progress(d,cx,cy,r,rem,total,c):
 f=max(0,min(1,rem/max(1,total)));d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=(25,55,80),width=14);d.arc((cx-r,cy-r,cx+r,cy+r),-90,-90+360*f,fill=c,width=14);tx(d,(cx,cy-8),fmt(rem),62,WHITE,True,'mm');tx(d,(cx,cy+58),'TIME REMAINING',20,MUTED,True,'mm')
def base(kind):
 im=Image.new('RGBA',(W,H),BG);glow(im,(860,145),(0,110,255),220,120);glow(im,(120,1030),(0,225,180),250,75);glow(im,(790,1080),(255,175,30),230,45);d=ImageDraw.Draw(im,'RGBA');rr(d,(35,35,W-35,H-35),48,(3,8,20,230),outline=(*CYAN,230),width=4);rr(d,(55,55,W-55,H-55),38,(6,14,30,225),outline=(90,135,190,70),width=2);tx(d,(85,82),'PRIYANITHAN AI',25,(130,205,255),True);tx(d,(85,118),'CANDICE',58,WHITE,True);tx(d,(87,178),'AI TRADING BOT',22,GOLD,True);d.ellipse((808,83,932,207),outline=(*CYAN,65),width=5);d.ellipse((848,123,892,167),fill=(*CYAN,210));return im,d
def card(kind='selected',asset='ASIA_X',direction='',confidence=0,expiry=0,reason='',**kw):
 im,d=base(kind);accent={'selected':GREEN,'signal':CYAN,'expiry':RED,'result':PURPLE,'recovery':GREEN,'session':ORANGE,'status':CYAN}.get(kind,CYAN);title={'selected':'ASSET SELECTED','signal':'NEW AI SIGNAL','expiry':'EXPIRY REACHED','result':'TRADE RESULT','recovery':'SHORT RECOVERY','session':'SIGNAL SESSION','status':'SYSTEM STATUS'}.get(kind,'CANDICE UPDATE');rr(d,(70,235,954,1045),32,(5,17,31,238),outline=(*accent,150),width=2);tx(d,(100,285),title,40,accent,True);tx(d,(100,365),asset,62,GOLD,True);rr(d,(710,325,930,375),22,(30,25,5,220),outline=GOLD,width=2);tx(d,(820,350),'OTC • FLEX',20,GOLD,True,'mm')
 if kind=='selected':
  vals=[('Fresh 1-minute candle','VERIFIED',GREEN),('Asset select time',kw.get('selected_time','—'),CYAN),('Candice AI analysis','ON',GREEN),('Research','24/7',CYAN),('Next signal window','NEXT 5 MIN',WHITE),('AI duration','2 / 3 / 5 / 10 / 15 MIN',WHITE)];y=470
  for a,b,c in vals:tx(d,(100,y),a,24,MUTED);tx(d,(920,y),b,23,c,True,'ra');d.line((95,y+38,925,y+38),fill=(50,100,140,80));y+=78
  tx(d,(512,930),'ASSET READY FOR CANDICE RESEARCH',27,GREEN,True,'mm')
 elif kind=='signal':
  c=GREEN if direction=='UP' else RED if direction=='DOWN' else WHITE;tx(d,(100,470),direction or 'WAIT',86,c,True);chart(d,(585,440,925,520),direction!='DOWN');vals=[('Signal time',kw.get('signal_time','—'),CYAN),('Entry',kw.get('entry','—'),WHITE),('Duration',f'{expiry} MIN',WHITE),('Candice AI',f'APPROVED • {confidence}%',GREEN)];y=575
  for a,b,c2 in vals:tx(d,(100,y),a,23,MUTED);tx(d,(470,y),b,23,c2,True);y+=58
  rem=max(0,int(kw.get('remaining',expiry*60)));progress(d,512,825,145,rem,max(1,int(kw.get('total_seconds',expiry*60))),c);tx(d,(512,1000),f'EXPIRY • {kw.get("expiry_time","VERIFYING")}',22,ORANGE,True,'mm')
 elif kind=='expiry':
  tx(d,(512,455),'EXPIRY REACHED',52,RED,True,'mm');vals=[('Direction',direction or '—',GREEN if direction=='UP' else RED),('Entry',kw.get('entry','—'),WHITE),('Duration',f'{expiry} MIN',WHITE),('Expiry boundary',kw.get('expiry_time','—'),ORANGE),('Reached at',kw.get('reached_time','—'),CYAN)];y=560
  for a,b,c in vals:tx(d,(100,y),a,23,MUTED);tx(d,(520,y),b,23,c,True);y+=62
  tx(d,(512,900),'VERIFYING CLOSED EXPIRY CANDLE…',27,ORANGE,True,'mm')
 elif kind=='result':
  result=direction or 'UNRESOLVED';c=GREEN if result=='WIN' else RED if result=='LOSS' else ORANGE
  # Professional outcome hero: status pill + price journey + verification seal.
  rr(d,(100,420,924,535),28,(7,25,39,245),outline=(*c,180),width=3);tx(d,(145,477),'FINAL MARKET OUTCOME',21,MUTED,True,'lm');tx(d,(870,477),result,58,c,True,'rm')
  tx(d,(512,620),result,104,c,True,'mm');tx(d,(512,685),'VERIFIED',22,GREEN if result!='UNRESOLVED' else ORANGE,True,'mm')
  rr(d,(100,735,924,845),25,(7,22,38,245),outline=(60,105,145,100),width=2);tx(d,(145,770),'ENTRY',18,MUTED,True);tx(d,(145,812),kw.get('entry','—'),28,WHITE,True);tx(d,(512,770),'EXPIRY',18,MUTED,True,'mm');tx(d,(512,812),kw.get('exit','—'),28,WHITE,True,'mm');tx(d,(870,770),'DURATION',18,MUTED,True,'rm');tx(d,(870,812),f'{expiry} MIN',28,CYAN,True,'rm')
  tx(d,(512,895),'✓  CANDLE-CLOSED VERIFICATION',23,GREEN,True,'mm');tx(d,(512,935),kw.get('result_time','—'),19,MUTED,False,'mm');tx(d,(512,985),'MARKET OUTCOME  •  NOT BROKER ACCOUNT P/L',18,MUTED,True,'mm')
 elif kind=='recovery':
  rem=max(0,int(kw.get('remaining',300)));progress(d,512,620,135,rem,max(1,int(kw.get('total_seconds',300))),CYAN);tx(d,(512,820),'RECOVERY PROTECTION ACTIVE',30,GREEN,True,'mm');tx(d,(512,875),'No signal until recovery check completes.',20,MUTED,False,'mm')
 elif kind=='session':
  act=bool(kw.get('active',False));rem=max(0,int(kw.get('remaining',0)));total=max(1,int(kw.get('total_seconds',10800)));tx(d,(100,500),'Session start',23,MUTED);tx(d,(920,500),kw.get('session_start','—'),22,GREEN,True,'ra');tx(d,(100,565),'Session end',23,MUTED);tx(d,(920,565),kw.get('session_end','—'),22,RED,True,'ra');progress(d,512,770,130,rem,total,GREEN if act else ORANGE);tx(d,(512,960),'SIGNAL SESSION ACTIVE' if act else 'RESEARCH ONLY',30,GREEN if act else ORANGE,True,'mm')
 else:tx(d,(512,510),'CANDICE AI',55,GREEN,True,'mm');tx(d,(512,600),kw.get('status_text','System online.'),24,WHITE,False,'mm')
 d.line((75,1070,W-75,1070),fill=(*accent,120),width=2);tx(d,(512,1110),'CANDICE AI • LIVE MARKET • MANUAL TRADE ONLY',23,GOLD,True,'mm');tx(d,(512,1150),'FLEX / FIXED-TIME  •  AUTO-TRADE OFF  •  MARTINGALE OFF',18,WHITE,True,'mm');tx(d,(512,1190),'OBSERVE • ANALYZE • COMPARE • LEARN • DECIDE • MONITOR • IMPROVE',14,GREEN,True,'mm');return im.convert('RGB')
def png_bytes(**kwargs):
 b=io.BytesIO();card(**kwargs).save(b,'PNG',optimize=True);b.seek(0);b.name='candice-update.png';return b
def gif_bytes(**kwargs):
 im=card(**kwargs);fs=[]
 for i in range(8):
  f=im.copy();d=ImageDraw.Draw(f,'RGBA');y=80+int((H-180)*i/8);d.line((75,y,W-75,y),fill=(0,220,255,65),width=3);r=7+int(4*(1+math.sin(i*math.pi/4)));d.ellipse((870-r,145-r,870+r,145+r),outline=(0,220,255,150),width=4);fs.append(f)
 b=io.BytesIO();fs[0].save(b,'GIF',save_all=True,append_images=fs[1:],duration=140,loop=0,optimize=True);b.seek(0);b.name='candice.gif';return b
def update_gif_bytes():return gif_bytes(kind='status',asset='SYSTEM',status_text='CANDICE visual model active')