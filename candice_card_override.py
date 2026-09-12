from io import BytesIO
from datetime import datetime
from math import sin


def install(app):
    def card(asset, direction, entry_ts, candle_ts, expiry, confidence, timeframe, evidence, timer_text='', timer_label='ENTRY IN'):
        W, H = 1080, 1350
        down = str(direction).upper() == 'DOWN'
        accent = (255, 48, 82) if down else (25, 255, 125)
        cyan = (15, 205, 255)
        white = (245, 249, 255)
        muted = (155, 195, 225)
        img = app.Image.new('RGB', (W, H), (3, 10, 28))
        px = img.load()
        for y in range(H):
            for x in range(W):
                g1=max(0.0,1.0-(((x-820)**2+(y-250)**2)**0.5)/900.0)
                g2=max(0.0,1.0-(((x-180)**2+(y-1120)**2)**0.5)/800.0)
                px[x,y]=(int(3+5*g1),int(9+18*g1+4*g2),int(27+48*g1+10*g2))
        d=app.ImageDraw.Draw(img)
        def rr(box,fill=(7,25,48),outline=cyan,width=3,radius=24):
            d.rounded_rectangle(box,radius=radius,fill=fill,outline=outline,width=width)
        def txt(x,y,s,size,fill=white,bold=False,anchor=None):
            d.text((x,y),str(s),font=app._font(size,bold),fill=fill,anchor=anchor)
        def fit(s,maxw,size,bold=True): return app._fit_text(d,str(s),maxw,size,bold)
        rr((10,10,W-10,H-10),fill=(3,12,34),outline=(20,85,190),width=4,radius=34)
        d.ellipse((35,38,135,138),fill=(10,70,170),outline=cyan,width=3)
        d.arc((42,45,128,131),205,500,fill=(0,235,255),width=16)
        d.arc((42,45,128,131),20,150,fill=(170,20,255),width=16)
        txt(158,43,'CANDICE',58,white,True); txt(485,45,'AI',58,(30,170,255),True)
        txt(160,112,'T R A D I N G   S I G N A L',23,muted)
        txt(160,157,'A N A L Y Z E   •   F I L T E R   •   C O N F I R M   •   A L E R T',16,(120,190,225))
        d.rectangle((820,45,826,143),fill=(235,0,245))
        for y,s in ((47,'DISCIPLINE'),(76,'BRINGS'),(105,'CONSISTENT'),(134,'RESULTS')): txt(850,y,s,19,muted,True)
        rr((28,185,W-28,355),fill=(5,39,52),outline=(0,210,235),width=3,radius=24)
        txt(105,207,'ASSET',23,muted,True)
        name='Asia Composite Index' if asset=='ASIA_X' else str(asset)
        d.text((105,248),name,font=fit(name,850,50,True),fill=white)
        rr((105,312,250,347),fill=(3,25,45),outline=(0,150,230),width=2,radius=12); txt(122,317,asset,18,(60,195,255),True)
        for i,h in enumerate((28,52,78,105)):
            x=40+i*20; d.rounded_rectangle((x,326-h,x+14,326),radius=3,fill=white)
        pts=[]
        for i in range(15):
            x=510+i*31; yy=325-int(105*(i/14.0)**1.4)-int(18*sin(i*1.8)); pts.append((x,yy))
        d.line(pts,fill=(0,235,135),width=5)
        hero=(6,38,28) if not down else (48,9,25)
        rr((28,378,485,670),fill=hero,outline=accent,width=4,radius=28)
        txt(72,404,'↓' if down else '↑',155,accent,True); txt(205,470,'DOWN' if down else 'UP',76,white,True); txt(205,555,'TRADE DIRECTION',21,accent,True)
        cx,cy,r=760,515,126
        d.arc((cx-r,cy-r,cx+r,cy+r),205,338,fill=(0,85,65),width=24); d.arc((cx-r,cy-r,cx+r,cy+r),18,150,fill=accent,width=13)
        d.ellipse((cx-r+18,cy-r+18,cx+r-18,cy+r-18),fill=(2,16,25),outline=(0,235,180),width=3)
        txt(cx,422,'LIVE TIMER',22,white,True,'mm')
        tv=str(timer_text) if timer_text else f'{app.PRE_ENTRY_SECONDS:02d} SEC'
        txt(cx,493,tv,64 if ':' in tv else 58,accent,True,'mm')
        txt(cx,574,'TO ENTRY' if timer_label in {'ENTRY IN','ALERT'} else 'RUNNING',22,white,True,'mm')
        for i in range(8):
            x=875+i*22; top=520-i*16; bot=top+55+(i%3)*8; col=(20,225,130) if i%3 else (235,55,75)
            d.line((x+7,top-18,x+7,bot+10),fill=col,width=2); d.rectangle((x,top,x+14,bot),fill=col)
        txt(850,625,'Trade Smart',21,(25,240,160),True); txt(850,650,'Trade Better',21,(25,240,160),True)
        cards=[(28,'ENTRY TIME',datetime.fromtimestamp(entry_ts,app.UAE).strftime('%H:%M:%S'),cyan),(286,'CANDLE','1 MIN',(30,190,120)),(544,'EXPIRY',f'{expiry} MIN',(250,190,20)),(802,'LIVE TIMER',tv,(240,45,65))]
        for x,label,value,col in cards:
            rr((x,690,x+242,820),fill=(5,24,47),outline=col,width=3,radius=22); txt(x+18,710,label,18,muted,True); d.text((x+18,750),value,font=fit(value,205,32,True),fill=white)
            if label=='LIVE TIMER': txt(x+18,789,'RUNNING' if timer_label=='LIVE TIMER' else 'ALERT',16,accent,True)
        rr((28,835,390,950),fill=(5,25,46),outline=(10,155,230),width=3); rr((408,835,770,950),fill=(5,25,46),outline=(10,155,230),width=3); rr((788,835,1052,950),fill=(5,25,46),outline=(10,155,230),width=3)
        txt(55,855,'HUMAN BRAIN',20,(205,145,240),True); txt(55,898,'CONFIRMED',29,accent,True); d.ellipse((330,870,375,915),fill=accent); txt(352,892,'✓',25,(2,35,25),True,'mm')
        txt(435,855,'AI ANALYSIS',20,(150,195,235),True); txt(435,898,'APPROVED',29,accent,True); d.ellipse((710,870,755,915),fill=accent); txt(732,892,'✓',25,(2,35,25),True,'mm')
        txt(815,855,'CONFIDENCE',20,(150,195,235),True); txt(815,891,f'{int(confidence)}%',43,accent,True)
        rr((28,968,W-28,1148),fill=(4,21,43),outline=(10,120,205),width=3,radius=24); txt(58,987,'SIGNAL REASONS',27,white,True)
        for i in range(18):
            x=620+i*22; top=1030-int(55*sin(i*.75)+i*1.2); bot=top+35+(i%4)*7; col=(20,130,110) if i%4 else (130,45,75)
            d.line((x+6,top-12,x+6,bot+12),fill=col,width=2); d.rectangle((x,top,x+12,bot),fill=col)
        items=list(evidence or ())[:3] or ['Multi-indicator alignment verified']; ry=1030; dots=[(30,235,160),(30,180,240),(250,195,25)]
        for i,item in enumerate(items):
            d.ellipse((58,ry+5,78,ry+25),fill=dots[i%3])
            for line in app._wrap_lines(d,item,490,app._font(20))[:2]: txt(95,ry,line,20,white); ry+=26
            ry+=7
        txt(850,1110,'Small Steps',18,(0,225,255),True); txt(850,1130,'Big Results',18,(0,225,255),True)
        rr((28,1170,W-28,1260),fill=(4,48,34),outline=(25,235,130),width=3,radius=22); txt(55,1185,'✓',45,accent,True); txt(115,1185,'MANUAL TRADE ONLY',27,accent,True); txt(115,1220,'AUTO-TRADE OFF  •  DEMO MODE',18,muted,True)
        d.line((665,1184,665,1247),fill=(20,235,190),width=2); txt(695,1188,'1m + 3m + 5m + 10m + 15m',18,white,True); txt(855,1220,'TRADE RESPONSIBLY',16,(20,230,150),True)
        txt(55,1280,'CANDICE AI',24,white,True); txt(55,1310,'YOUR TRADING PARTNER',16,muted); txt(360,1285,'DISCIPLINE',17,muted,True); txt(535,1285,'PLAN',17,muted,True); txt(670,1285,'PROFIT',17,muted,True)
        d.arc((-150,1280,650,1450),190,355,fill=(30,80,255),width=4); d.arc((360,1270,1220,1430),190,350,fill=(180,20,255),width=4); d.arc((580,1290,1350,1450),185,345,fill=(20,220,255),width=3)
        bio=BytesIO(); img.save(bio,format='JPEG',quality=88,optimize=True); bio.seek(0); return bio
    app._card_base=card
    print('CANDICE REFERENCE CARD OVERRIDE: ACTIVE')
