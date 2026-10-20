(()=> {
  'use strict';
  const P=['#faf5e8',
  '#202730',
  '#e66b42',
  '#49a69d'];
  const recipe= {
    "编辑练习":"把三物体改成六物体并将 value 提至100；观察布局按最大半径收紧间距，检查每章边界与标签，再保留 transition_frames+6 的最短章窗。",
    "适用/不适用":"关系与材质说明；不用于替代真实三维成片",
    "视觉隐喻":"从分散零件到共同系统",
    "4–6拍":["提出分散问题",
    "并列识别角色",
    "靠近并建立连接",
    "关系整体落定",
    "保留结果阅读"],
    "不可删特征":"体积轮廓、材质对照、三章关系变化",
    "可替语义":"物体角色、值、形状、材质、连接图",
    "动作/转场/声音点":"过渡段按同一进度移动；连接出现可作声音点，页面没有音轨",
    "失败反例":"把投影渐变当成真实PBR验收，或所有物体同速弹跳"
  };
  function common(d,N,h) {
    h.int(N,'frames',30,18000);
    h.palette(d.palette??P);
    h.label(d.title??d.mission,'title',70);
  }
  function header(c,d,h) {
    const p=d.palette??P;
    c.fillStyle=p[0];
    c.fillRect(0,0,1920,1080);
    h.text(c,d.title??d.mission,100,65,1720,62, {
      color:p[1],minSize:32
    });
    if(d.subtitle)h.text(c,d.subtitle,100,148,1720,30, {
      color:p[1],minSize:24
    });
  }
  window.MG_REFERENCE= {
    id:"soft-3d",
    title:"柔和三维：关系分镜参考",
    limits:"Canvas投影与材质示意；不是真实网格、灯光或PBR，不代替Blender逐帧渲染。没有声音。",
    examples:[ {
      "name":"零件成为系统",
      "frames":120,
      "data": {
        "title":"让零件成为系统",
        "subtitle":"分散的问题，连接的答案",
        "objects":[ {
          "label":"观察",
          "value":35,
          "shape":"sphere",
          "material":"ceramic",
          "roughness":0.28
        },
        {
          "label":"连接",
          "value":64,
          "shape":"torus",
          "material":"metal",
          "roughness":0.2
        },
        {
          "label":"形成",
          "value":90,
          "shape":"cube",
          "material":"matte",
          "roughness":0.65
        }],
        "palette":["#faf5e8",
        "#202730",
        "#e66b42",
        "#49a69d"],
        "scene": {
          "layout":"stage"
        },
        "scene_story":[ {
          "frame":0,
          "title":"01  各自为战",
          "layout":"scatter"
        },
        {
          "frame":40,
          "title":"02  建立连接",
          "layout":"assemble"
        },
        {
          "frame":80,
          "title":"03  形成共同系统",
          "layout":"result"
        }],
        "links":[[0,
        1],
        [1,
        2]],
        "beats": {
          "reveal":0,
          "settle":40,
          "resolve":96
        },
        "transition_frames":12,
        "transition":"camera-pullback",
        "pace":1
      }
    },
    {
      "name":"两种材质建立关系",
      "frames":120,
      "data": {
        "title":"让不同角色共同创作",
        "subtitle":"两种角色，一份共同创作",
        "objects":[ {
          "label":"灵感",
          "value":48,
          "shape":"sphere",
          "material":"ceramic",
          "roughness":0.23
        },
        {
          "label":"联结",
          "value":85,
          "shape":"torus",
          "material":"metal",
          "roughness":0.16
        }],
        "palette":["#f3eee5",
        "#302b29",
        "#cb7248",
        "#68948d"],
        "scene": {
          "layout":"arc"
        },
        "scene_story":[ {
          "frame":0,
          "title":"01  两种角色",
          "layout":"scatter"
        },
        {
          "frame":40,
          "title":"02  找到共同位置",
          "layout":"assemble"
        },
        {
          "frame":80,
          "title":"03  联结产生价值",
          "layout":"result"
        }],
        "links":[[0,
        1]],
        "beats": {
          "reveal":0,
          "settle":40,
          "resolve":96
        },
        "transition_frames":12,
        "transition":"hold",
        "pace":1.3
      }
    }],
    recipe,
    // Validate content roles and reserve six settled samples in every chapter.
    validate:(d,N,h)=> {
      common(d,N,h);
      h.num(d.pace??1,'pace',.25,4);
      if(!Array.isArray(d.objects)||d.objects.length<1||d.objects.length>6)throw Error('objects 1..6');
      d.objects.forEach(o=> {
        h.label(o.label,'role',16);
        h.num(o.value,'value',0,100);
        if(!['sphere','cube','torus'].includes(o.shape))throw Error('shape');
        h.num(o.roughness,'roughness',0,1);
      });
      if(!Array.isArray(d.scene_story)||d.scene_story.length<2)throw Error('chapters');
      d.scene_story.forEach((s,i)=> {
        h.int(s.frame,'chapter',i?d.scene_story[i-1].frame+1:0,i?N-13:0);
        h.label(s.title,'chapter',40);
        if(!['scatter','assemble','result'].includes(s.layout))throw Error('layout');
      });
      h.int(d.transition_frames??12,'transition',2,30);
      d.scene_story.forEach((s,i)=> {
        const end=i+1<d.scene_story.length?d.scene_story[i+1].frame:N;
        if(end-s.frame<(d.transition_frames??12)+6)throw Error('each chapter needs transition completion and six settled frames');
      });
      (d.links??[]).forEach(l=> {
        if(l.length!==2||l[0]===l[1])throw Error('link');
        l.forEach(v=>h.int(v,'link',0,d.objects.length-1));
      });
    },
    // Choose the chapter from the requested frame; interpolate only its transition.
    render:(c,d,f,N,h)=> {
      header(c,d,h);
      const p=d.palette??P,
      chs=d.scene_story,
      k=chs.reduce((v,s,i)=>f>=s.frame?i:v,0),
      s=chs[k],
      prev=chs[Math.max(0,k-1)],
      t=h.smooth(h.clamp((f-s.frame)/(d.transition_frames??12))**(d.pace??1)),
      pos=(layout,i)=> {
        let n=d.objects.length,
        maxRadius=Math.max(...d.objects.map(o=>62+o.value*.48)),
        margin=maxRadius*1.2+12,
        spacing=n>1?Math.min(310,(1720-2*margin-160)/(n-1)):0,
        scatter=Math.max(0,Math.min(80,(spacing-2*maxRadius)/2)),
        x=960+(i-(n-1)/2)*spacing,
        y=600;
        if(layout==='scatter') {
          x+=i%2?scatter:-scatter;
          y+=i%2?-85:80;
        }
        if(layout==='result') {
          x=960+Math.cos(-Math.PI/2+i*Math.PI*2/n)*290;
          y=600+Math.sin(-Math.PI/2+i*Math.PI*2/n)*120;
        }
        return[x,
        y];
      },
      pts=d.objects.map((o,i)=> {
        let a=pos(prev.layout,i),b=pos(s.layout,i);
        return[a[0]+(b[0]-a[0])*t,a[1]+(b[1]-a[1])*t];
      });
      h.text(c,s.title,100,230,1720,38, {
        color:p[1]
      });
      c.lineWidth=14;
      c.strokeStyle=p[3];
      if(k>0)for(const[a,b]of d.links??[]) {
        c.beginPath();
        c.moveTo(...pts[a]);
        c.lineTo(...pts[b]);
        c.stroke();
      }
      // Draw relationship links first; projected objects and fixed reading labels sit above.
      d.objects.forEach((o,i)=> {
        const[x,y]=pts[i],r=62+o.value*.48;
        c.save();
        c.translate(x,y);
        c.scale(1,.28);
        h.circle(c,12,r+48,r*1.2,p[1]+'22');
        c.restore();
        let g=c.createRadialGradient(x-r*.35,y-r*.45,2,x,y,r);
        g.addColorStop(0,o.material==='metal'?'#ffffff':p[0]);
        g.addColorStop(Math.max(.15,o.roughness),p[2+i%2]);
        g.addColorStop(1,p[1]);
        if(o.shape==='cube') {
          c.fillStyle=g;
          c.fillRect(x-r,y-r,r*2,r*2);
        }
        else if(o.shape==='torus') {
          c.lineWidth=r*.48;
          c.strokeStyle=g;
          c.beginPath();
          c.ellipse(x,y,r*.72,r*.72,0,0,Math.PI*2);
          c.stroke();
        }
        else h.circle(c,x,y,r,g);
        h.text(c,o.label,100+i*1720/d.objects.length,880,1680/d.objects.length,36, {
          color:p[1]
        });
        h.text(c,String(o.value),100+i*1720/d.objects.length,935,1680/d.objects.length,30, {
          color:p[3]
        });
      });
    }
  };
})();
