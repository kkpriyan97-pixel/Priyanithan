"""Signal logo delivery layer.
Sends a lightweight 3D-style PRIYANITHAN AI TRADING image before approved signals.
"""
import io, re, sys, time

def _app():
    m=sys.modules.get("__main__")
    if m is not None and getattr(m,"__file__","").endswith("app.py"): return m
    return sys.modules.get("app")

def _image():
    try:
        import matplotlib.pyplot as plt
        fig=plt.figure(figsize=(7,3),dpi=120)
        ax=fig.add_axes([0,0,1,1]); ax.axis("off"); ax.set_facecolor("#07111f")
        for i,alpha in enumerate((.10,.16,.22,.30)):
            ax.text(.505+i*.006,.54-i*.01,"P",fontsize=82,weight="bold",ha="center",va="center",color=(1,.75,.15),alpha=alpha)
        ax.text(.5,.54,"P",fontsize=82,weight="bold",ha="center",va="center",color="#f5c542")
        ax.text(.63,.57,"PRIYANITHAN",fontsize=22,weight="bold",ha="center",color="white")
        ax.text(.63,.40,"AI TRADING",fontsize=16,weight="bold",ha="center",color="#4dd0e1")
        b=io.BytesIO(); fig.savefig(b,format="png",bbox_inches="tight",facecolor="#07111f"); plt.close(fig); b.seek(0); return b
    except Exception:return None

def _install():
    for _ in range(1800):
        a=_app()
        if a:
            sender=getattr(a,"send_to_recipients",None)
            if sender and not getattr(a,"_SIGNAL_LOGO_LAYER",False):
                async def wrapped(bot,text):
                    if re.search(r"🔥\s*PRIYANITHAN AI SIGNAL\s*🔥",str(text)):
                        img=_image()
                        if img:
                            for cid in a.recipients():
                                try: await bot.send_photo(chat_id=cid,photo=img,caption="🖼️ PRIYANITHAN AI TRADING")
                                except Exception as e: a.log.warning("3D logo send failed chat=%s: %s",cid,e)
                    return await sender(bot,text)
                wrapped._SIGNAL_LOGO_LAYER=True
                a.send_to_recipients=wrapped; a._SIGNAL_LOGO_LAYER=True; a.log.info("3D SIGNAL LOGO DELIVERY ACTIVE"); return
        time.sleep(1)

try:
    import threading
    threading.Thread(target=_install,name="signal-logo-layer",daemon=True).start()
except Exception:pass
