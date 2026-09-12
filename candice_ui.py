from __future__ import annotations
import io, math
from PIL import Image, ImageDraw, ImageFont, ImageFilter
W,H=1024,1280
WHITE=(245,250,255); MUTED=(150,185,215); CYAN=(0,215,255); BLUE=(35,110,255); GREEN=(45,255,145); GOLD=(255,205,65); RED=(255,70,85); ORANGE=(255,165,55); PURPLE=(190,105,255); BG=(2,8,18)

def font(n,b=False): return ImageFont.truetype('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',n)
def tx(d,xy,s,n,fill=WHITE,b=False,anchor='la'): d.text(xy,str(s),font=font(n,b),fill=fill,anchor=anchor)
def rr(d,box,r,fill,outline=None,width=1): d.rounded_rectangle(box,radius=r,fill=fill,outline=outline,width=width)
def glow(im,xy,rgb,r=180,a=100):
    lay=Image.new('RGBA',im.size,(0,0,0,0)); d=ImageDraw.Draw(lay); x,y=xy
    for q in range(r,8,-10): d.ellipse((x-q,y-q,x+q,y+q),fill=(*rgb,max(0,int(a*(1-q/r)**1.6))))
    im.alpha_composite(lay.filter(ImageFilter.GaussianBlur(20)))
def fmt(sec):
    sec=max(0,int(sec)); return f'{sec//3600:02d}:{(sec%3600)//60:02d}:{sec%60:02d}' if sec>=3600 else f'{sec//60:02d}:{sec%60:02d}'
def chart(d,box,bullish=True):
    x1,y1,x2,y2=box; prev=y2-28; step=(x2-x1)/12
    for i in range(11):
        x=x1+step*(i+1); y=max(y1+18,min(y2-18,prev+(-1 if bullish else 1)*(13+i*2)+math.sin(i*1.7)*18)); c=GREEN if y<prev else RED
        d.line((x,y-34,x,y+34),fill=c,width=5); d.rectangle((x-10,y-16,x+10,y+16),fill=c); prev=y
def progress(d,cx,cy,r,remaining,total,accent):
    frac=max(0,min(1,remaining/max(1,total))); d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=(25,55,80),width=14); d.arc((cx-r,cy-r,cx+r,cy+r),-90,-90+360*frac,fill=accent,width=14); tx(d,(cx,cy-10),fmt(remaining),64,WHITE,True,'mm'); tx(d,(cx,cy+60),'TIME REMAINING',21,MUTED,True,'mm')

def base(kind,asset):
    im=Image.new('RGBA',(W,H),BG); glow(im,(860,145),(0,110,255),210,120); glow(im,(120,1030),(0,225,180),240,80); glow(im,(790,1080),(255,175,30),230,45); d=ImageDraw.Draw(im,'RGBA')
    rr(d,(35,35,W-35,H-35),48,(3,8,20,228),outline=(*CYAN,230),width=4); rr(d,(55,55,W-55,H-55),38,(6,14,30,225),outline=(90,135,190,70),width=2)
    tx(d,(85,82),'PRIYANITHAN AI',25,(130,205,255),True,'la'); tx(d,(85,118),'CANDICE',58,WHITE,True,'la'); tx(d,(87,178),'AI TRADING BOT',22,GOLD,True,'la')
    cx,cy=870,145
    for r,a in ((94,25),(78,40),(62,65)): d.ellipse((cx-r,cy-r,cx+r,cy+r),outline=(*CYAN,a),width=5)
    d.ellipse((cx-25,cy-25,cx+25,cy+25),fill=(*CYAN,220))
    rr(d,(70,235,W-70,1045),32,(5,17,31,235),outline=(*CYAN,150),width=2)
    return im,d

def card(kind='selected',asset='ASIA_X',direction='',confidence=0,expiry=0,reason='',**kw):
    im,d=base(kind,asset)
    accents={'selected':GREEN,'signal':CYAN,'expiry':RED,'result':PURPLE,'recovery':GREEN,'session':ORANGE,'status':CYAN}; accent=accents.get(kind,CYAN)
    titles={'selected':'ASSET SELECTED','signal':'NEW AI SIGNAL','expiry':'EXPIRY REACHED','result':'TRADE RESULT','recovery':'SHORT RECOVERY','session':'SIGNAL SESSION','status':'SYSTEM STATUS'}
    tx(d,(100,285),titles.get(kind,'CANDICE UPDATE'),42,accent,True,'la')
    tx(d,(100,365),asset,62,GOLD,True,'la'); rr(d,(710,325,930,375),22,(30,25,5,220),outline=GOLD,width=2); tx(d,(820,350),'OTC • FLEX',20,GOLD,True,'mm')
    if kind=='selected':
        vals=[('Fresh 1-minute candle','VERIFIED',GREEN),('Asset select time',kw.get('selected_time','—'),CYAN),('Candice AI analysis','ON',GREEN),('Research','24/7',CYAN),('Next signal window','NEXT 5 MIN',WHITE),('AI duration','2 / 3 / 5 / 10 / 15 MIN',WHITE)]
        y=470
        for a,b,c in vals: tx(d,(100,y),a,24,MUTED,False,'la'); tx(d,(920,y),b,23,c,True,'ra'); d.line((95,y+38,925,y+38),fill=(50,100,140,80),width=1); y+=78
        tx(d,(512,930),'ASSET READY FOR CANDICE RESEARCH',27,GREEN,True,'mm')
    elif kind=='signal':
        c=GREEN if direction=='UP' else RED if direction=='DOWN' else WHITE; tx(d,(100,470),direction or 'WAIT',86,c,True,'la'); chart(d,(585,440,925,520),direction!='DOWN')
        vals=[('Signal time',kw.get('signal_time','—'),CYAN),('Entry',kw.get('entry','—'),WHITE),('Duration',f'{expiry} MIN',WHITE),('Candice AI',f'APPROVED • {confidence}%',GREEN)]
        y=575
        for a,b,c2 in vals: tx(d,(100,y),a,23,MUTED,False,'la'); tx(d,(470,y),b,23,c2,True,'la'); y+=58
        total=max(1,int(kw.get('total_seconds',expiry*60))); rem=max(0,int(kw.get('remaining',total))); progress(d,512,825,145,rem,total,c)
        tx(d,(512,1000),f'EXPIRY • {kw.get("expiry_time","VERIFYING")}',22,ORANGE,True,'mm')
    elif kind=='expiry':
        tx(d,(512,455),'EXPIRY REACHED',52,RED,True,'mm'); vals=[('Direction',direction or '—',GREEN if direction=='UP' else RED),('Entry',kw.get('entry','—'),WHITE),('Duration',f'{expiry} MIN',WHITE),('Expiry boundary',kw.get('expiry_time','—'),ORANGE),('Reached at',kw.get('reached_time','—'),CYAN)]; y=560
        for a,b,c in vals: tx(d,(100,y),a,23,MUTED,False,'la'); tx(d,(520,y),b,23,c,True,'la'); y+=62
        tx(d,(512,900),'VERIFYING CLOSED EXPIRY CANDLE…',27,ORANGE,True,'mm')
    elif kind=='result':
        result=direction or 'UNRESOLVED'; c=GREEN if result=='WIN' else RED if result=='LOSS' else ORANGE; tx(d,(512,460),result,92,c,True,'mm'); vals=[('Entry',kw.get('entry','—'),WHITE),('Expiry price',kw.get('exit','—'),WHITE),('Duration',f'{expiry} MIN',WHITE),('Verification',kw.get('verification','candle-closed'),GREEN),('Result time',kw.get('result_time','—'),CYAN)]; y=575
        for a,b,c2 in vals: tx(d,(100,y),a,23,MUTED,False,'la'); tx(d,(520,y),b,23,c2,True,'la'); y+=62
        tx(d,(512,925),'MARKET OUTCOME • NOT BROKER ACCOUNT P/L',20,MUTED,True,'mm')
    elif kind=='recovery':
        rem=max(0,int(kw.get('remaining',300))); total=max(1,int(kw.get('total_seconds',300))); vals=[('Recovery window',kw.get('recovery','15 MIN'),GREEN),('Quick recovery',kw.get('quick_recovery','5 MIN'),ORANGE)]; y=500
        for a,b,c in vals: tx(d,(100,y),a,24,MUTED,False,'la'); tx(d,(520,y),b,24,c,True,'la'); y+=65
        progress(d,512,720,135,rem,total,CYAN); tx(d,(512,950),'RECOVERY PROTECTION ACTIVE',29,GREEN,True,'mm')
    elif kind=='session':
        act=bool(kw.get('active',False)); rem=max(0,int(kw.get('remaining',0))); total=max(1,int(kw.get('total_seconds',10800))); vals=[('Session start',kw.get('session_start','—'),GREEN),('Session end',kw.get('session_end','—'),RED),('Next session',kw.get('next_session','—'),CYAN)]; y=500
        for a,b,c in vals: tx(d,(100,y),a,24,MUTED,False,'la'); tx(d,(520,y),b,22,c,True,'la'); y+=65
        progress(d,512,760,135,rem,total,GREEN if act else ORANGE); tx(d,(512,965),'SIGNAL SESSION ACTIVE' if act else 'RESEARCH ONLY',30,GREEN if act else ORANGE,True,'mm')
    else:
        tx(d,(512,500),'CANDICE AI',55,GREEN,True,'mm'); tx(d,(512,590),kw.get('status_text','System online.'),25,WHITE,False,'mm')
    d.line((75,1070,W-75,1070),fill=(*accent,120),width=2); tx(d,(512,1110),'CANDICE AI • LIVE MARKET • MANUAL TRADE ONLY',23,GOLD,True,'mm'); tx(d,(512,1150),'FLEX / FIXED-TIME  •  AUTO-TRADE OFF  •  MARTINGALE OFF',18,WHITE,True,'mm'); tx(d,(512,1190),'OBSERVE • ANALYZE • COMPARE • LEARN • DECIDE • MONITOR • IMPROVE',14,GREEN,True,'mm')
    return im.convert('RGB')

def png_bytes(**kwargs):
    b=io.BytesIO(); card(**kwargs).save(b,format='PNG',optimize=True); b.seek(0); b.name='candice-update.png'; return b

def gif_bytes(**kwargs):
    image=card(**kwargs); frames=[]
    for i in range(8):
        f=image.copy(); dd=ImageDraw.Draw(f,'RGBA'); sy=80+int((H-180)*i/8); dd.line((75,sy,W-75,sy),fill=(0,220,255,70),width=3); rr=7+int(4*(1+math.sin(i*math.pi/4))); dd.ellipse((850-rr,145-rr,850+rr,145+rr),outline=(0,220,255,160),width=4); frames.append(f)
    b=io.BytesIO(); frames[0].save(b,format='GIF',save_all=True,append_images=frames[1:],duration=140,loop=0,optimize=True); b.seek(0); b.name='candice.gif'; return b

def update_gif_bytes(): return gif_bytes(kind='status',asset='SYSTEM',status_text='CANDICE visual model active')
