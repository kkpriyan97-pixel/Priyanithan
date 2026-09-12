from __future__ import annotations
import io, math, re
from PIL import Image, ImageDraw, ImageFont, ImageFilter
W,H=720,900

def font(n,b=False):
    p='/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf' if b else '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf'
    return ImageFont.truetype(p,n)
F1,F2,F3,F4=font(34,1),font(26,1),font(56,1),font(20)

def glow(im,xy,r,c):
    lay=Image.new('RGBA',im.size,(0,0,0,0)); d=ImageDraw.Draw(lay); x,y=xy
    for rr in range(r,5,-6): d.ellipse((x-rr,y-rr,x+rr,y+rr),fill=(*c,max(0,int(70*(1-rr/r)**1.7))))
    im.alpha_composite(lay.filter(ImageFilter.GaussianBlur(12)))

def card(kind='selected',asset='ASIA_X',direction='',confidence=0,expiry=0,reason=''):
    im=Image.new('RGBA',(W,H),(2,7,18,255)); glow(im,(600,120),170,(0,150,255)); glow(im,(100,760),180,(0,220,170)); d=ImageDraw.Draw(im)
    d.rounded_rectangle((18,18,W-18,H-18),30,fill=(5,13,29,245),outline=(40,170,255,220),width=3)
    d.text((48,48),'CANDICE',font=F1,fill=(255,205,70)); d.text((49,91),'AI TRADING BOT',font=F4,fill=(180,215,245)); d.rounded_rectangle((535,48,672,96),22,fill=(5,40,75),outline=(0,210,255),width=2); d.text((604,72),'FLEX MODE',font=font(18,1),fill=(0,225,255),anchor='mm')
    d.ellipse((45,145,250,350),fill=(3,18,38),outline=(0,185,255),width=4); glow(im,(147,247),85,(0,130,255)); d.ellipse((72,172,223,323),outline=(255,184,40),width=5); d.arc((84,184,211,311),20,310,fill=(255,205,55),width=8); d.text((147,247),'AI',font=font(42,1),fill=(245,250,255),anchor='mm')
    d.text((280,165),asset,font=F3,fill=(245,250,255)); d.rounded_rectangle((570,164,672,208),18,fill=(35,25,4),outline=(255,190,45),width=2); d.text((621,186),'OTC',font=font(18,1),fill=(255,210,80),anchor='mm')
    d.rounded_rectangle((275,225,672,283),18,fill=(4,55,45),outline=(0,245,170),width=3); d.text((474,254),'✓  ASSET SELECTED',font=font(20,1),fill=(70,255,190),anchor='mm')
    rows=[('MARKET',asset),('SESSION','FLEX'),('MODE','FIXED-TIME')]
    y=310
    for a,b in rows:
        d.rounded_rectangle((45,y,W-45,y+62),16,fill=(7,25,45),outline=(45,100,155),width=1); d.text((68,y+31),a,font=F4,fill=(130,180,220),anchor='lm'); d.text((W-70,y+31),b,font=font(21,1),fill=(245,250,255),anchor='rm'); y+=72
    d.rounded_rectangle((45,535,W-45,620),18,fill=(7,18,34),outline=(35,80,120),width=2); d.text((68,560),'FOREX',font=F4,fill=(150,180,210)); d.text((68,595),'OFF',font=font(22,1),fill=(255,80,90)); d.text((278,560),'AUTO-TRADE',font=F4,fill=(150,180,210)); d.text((278,595),'OFF',font=font(22,1),fill=(255,80,90)); d.text((530,560),'MARTINGALE',font=font(18,1),fill=(150,180,210)); d.text((530,595),'OFF',font=font(22,1),fill=(255,80,90))
    if kind=='signal':
        d.rounded_rectangle((45,645,W-45,745),20,fill=(8,35,65),outline=(0,220,255),width=3); d.text((68,680),direction,font=font(34,1),fill=(0,230,255)); d.text((68,720),f'{confidence}% confidence  •  {expiry} min',font=font(19),fill=(225,240,250))
    elif kind=='result':
        d.text((W/2,685),direction,font=font(45,1),fill=(70,255,150) if direction=='WIN' else (255,80,90),anchor='mm'); d.text((W/2,730),reason[:55],font=font(17),fill=(210,225,240),anchor='mm')
    else: d.text((W/2,690),'READY • LIVE ANALYSIS',font=font(24,1),fill=(0,220,255),anchor='mm')
    d.line((45,790,W-45,790),fill=(0,170,255,100),width=2); d.text((W/2,830),'TRADE SMARTER WITH CANDICE',font=font(18,1),fill=(255,205,70),anchor='mm'); d.text((W/2,860),'MANUAL TRADE ONLY',font=font(15),fill=(150,200,235),anchor='mm')
    return im.convert('RGB')

def gif_bytes(**kwargs):
    base=card(**kwargs); frames=[]
    for i in range(8):
        f=base.copy(); d=ImageDraw.Draw(f,'RGBA'); y=40+int(800*((i%8)/8)); d.line((45,y,W-45,y),fill=(0,220,255,45),width=2); frames.append(f)
    b=io.BytesIO(); frames[0].save(b,format='GIF',save_all=True,append_images=frames[1:],duration=140,loop=0,optimize=True); b.seek(0); return b
