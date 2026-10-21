(()=> {
  'use strict';
  // Equal-length world axes: ground axes at +30/+150 degrees, vertical at -90.
  function projectIso(x,y,z=0) {
    const axis=150;
    return[960+(x-y)*axis*Math.sqrt(3)/2,
    420+(x+y)*axis/2-z*axis];
  }
  const {storyPalette: P, validateStoryTitle: common, storyHeader: header} = window.MGSceneHelpers;
  const recipe= {
    "编辑练习":"将第三建筑的依赖改为另一节点；把 base 后移到该依赖 activate 之后，依次后移 wall/roof/activate，并在片尾保留6帧亮窗。",
    "适用/不适用":"空间依赖与建设过程；不用于精密工程图",
    "视觉隐喻":"一座有因果的小世界",
    "4–6拍":["展示场地",
    "铺基础",
    "立结构与封顶",
    "能源激活",
    "关系流动并留读"],
    "不可删特征":"等轴投影、遮挡排序、基础先于墙体、依赖先于激活",
    "可替语义":"网格、建筑角色、时窗、依赖与流量",
    "动作/转场/声音点":"封顶与亮窗为离散拍点；页面无音轨",
    "失败反例":"先亮图例后整片无窗，或前后建筑遮挡顺序错误"
  };

  window.MG_REFERENCE= {
    geometry: {
      project:projectIso
    },
    id:"isometric",
    title:"等距微世界：从依赖到激活",
    limits:"真实等距投影的Canvas构图参考；不是工程模拟或媒体路径像素等价副本。无音轨。",
    examples:[ {
      "name":"社区能源与生活",
      "frames":240,
      "data": {
        "title":"让社区连接起来",
        "subtitle":"能源先到位，生活与创造随后发生",
        "grid":[4,
        4],
        "seed":17,
        "nodes":[ {
          "id":"energy",
          "label":"能源站",
          "kind":"hub",
          "x":0,
          "y":0,
          "height":0.95,
          "base":0,
          "wall":12,
          "roof":30,
          "activate":38
        },
        {
          "id":"homes",
          "label":"生活区",
          "kind":"home",
          "x":3,
          "y":0,
          "height":1.2,
          "base":42,
          "wall":54,
          "roof":78,
          "activate":86,
          "depends_on":["energy"]
        },
        {
          "id":"lab",
          "label":"创作室",
          "kind":"lab",
          "x":2,
          "y":3,
          "height":0.85,
          "base":90,
          "wall":102,
          "roof":126,
          "activate":134,
          "depends_on":["homes"]
        },
        {
          "id":"park",
          "label":"共享花园",
          "kind":"garden",
          "x":0,
          "y":3,
          "height":0.7,
          "base":90,
          "wall":102,
          "roof":120,
          "activate":128,
          "depends_on":["energy"]
        }],
        "flows":[ {
          "from":"energy",
          "to":"homes",
          "in":90,
          "out":240
        },
        {
          "from":"homes",
          "to":"lab",
          "in":138,
          "out":240
        },
        {
          "from":"homes",
          "to":"park",
          "in":138,
          "out":240
        }]
      }
    },
    {
      "name":"校园知识网络",
      "frames":210,
      "data": {
        "title":"知识如何成为共同成果",
        "subtitle":"学习启动研究，研究汇入分享网络",
        "grid":[4,
        3],
        "palette":["#edf8fa",
        "#192c37",
        "#649cc1",
        "#8fc9b1"],
        "seed":-9,
        "nodes":[ {
          "id":"school",
          "label":"学习",
          "kind":"home",
          "x":0,
          "y":1,
          "height":1.4,
          "base":0,
          "wall":10,
          "roof":28,
          "activate":36
        },
        {
          "id":"research",
          "label":"研究",
          "kind":"lab",
          "x":1,
          "y":0,
          "height":1.0,
          "base":40,
          "wall":52,
          "roof":72,
          "activate":80,
          "depends_on":["school"]
        },
        {
          "id":"commons",
          "label":"交流",
          "kind":"hub",
          "x":2,
          "y":1,
          "height":1.7,
          "base":84,
          "wall":96,
          "roof":118,
          "activate":126,
          "depends_on":["research"]
        },
        {
          "id":"garden",
          "label":"共享空间",
          "kind":"garden",
          "x":1,
          "y":2,
          "height":0.8,
          "base":84,
          "wall":96,
          "roof":114,
          "activate":122,
          "depends_on":["school"]
        },
        {
          "id":"studio",
          "label":"实践",
          "kind":"lab",
          "x":3,
          "y":0,
          "height":0.8,
          "base":130,
          "wall":142,
          "roof":162,
          "activate":170,
          "depends_on":["commons"]
        }],
        "flows":[ {
          "from":"school",
          "to":"research",
          "in":84,
          "out":210
        },
        {
          "from":"research",
          "to":"commons",
          "in":130,
          "out":210
        },
        {
          "from":"commons",
          "to":"studio",
          "in":174,
          "out":210
        }]
      }
    }],
    recipe,
    // Reject impossible build dependencies and activation without six reading frames.
    validate:(d,N,h)=> {
      common(d,N,h);
      if(!Array.isArray(d.grid)||d.grid.length!==2)throw Error('grid');
      d.grid.forEach(v=>h.int(v,'grid',2,4));
      h.int(d.seed??0,'seed',-2147483648,2147483647);
      if(!Array.isArray(d.nodes)||d.nodes.length<2||d.nodes.length>12)throw Error('nodes');
      const ids=new Set(),
      cells=new Set();
      d.nodes.forEach(n=> {
        if(ids.has(n.id)||cells.has(n.x+','+n.y))throw Error('duplicate');
        ids.add(n.id);
        cells.add(n.x+','+n.y);
        h.label(n.label,'node',20);
        if(!['home','lab','hub','garden'].includes(n.kind))throw Error('kind');
        h.num(n.height,'height',.35,1.8);
        h.int(n.x,'x',0,d.grid[0]-1);
        h.int(n.y,'y',0,d.grid[1]-1);
        h.int(n.base,'base',0,N-19);
        h.int(n.wall,'wall',n.base+1,N-13);
        h.int(n.roof,'roof',n.wall+6,N-7);
        h.int(n.activate,'activate',n.roof+6,N-6);
      });
      d.nodes.forEach(n=>(n.depends_on||[]).forEach(id=> {
        const a=d.nodes.find(v=>v.id===id);
        if(!a||a.activate>n.base)throw Error('dependency');
      }));
      (d.flows??[]).forEach(q=> {
        const a=d.nodes.find(n=>n.id===q.from),b=d.nodes.find(n=>n.id===q.to);
        if(!a||!b||a===b)throw Error('flow endpoints');
        h.int(q.in,'flow in',Math.max(a.activate,b.activate),N-1);
        h.int(q.out,'flow out',q.in+1,N);
      });
    },
    // Establish the shared world grid before drawing buildings in depth order.
    render:(c,d,f,N,h)=> {
      header(c,d,h);
      const p=d.palette??P,
      project=projectIso;
      for(let x=0;
      x<d.grid[0];
      x++)for(let y=0;
      y<d.grid[1];
      y++)h.polygon(c,[[x-.44,y-.44],[x+.44,y-.44],[x+.44,y+.44],[x-.44,y+.44]].map(v=>project(...v)),p[3]+'33');
      [...d.nodes].sort((a,b)=>a.x+a.y-b.x-b.y||a.x-b.x).forEach(n=> {
        if(f<n.base)return;
        let b=h.ease((f-n.base)/Math.max(1,n.wall-n.base)),z=n.height*h.ease((f-n.wall)/(n.roof-n.wall)),q=[[-.35,-.35],[.35,-.35],[.35,.35],[-.35,.35]],at=(i,Z)=>project(n.x+q[i][0]*b,n.y+q[i][1]*b,Z);
        h.polygon(c,[at(1,0),at(2,0),at(2,z),at(1,z)],p[2]);
        h.polygon(c,[at(2,0),at(3,0),at(3,z),at(2,z)],p[1]);
        h.polygon(c,q.map((_,i)=>at(i,z)),f>=n.roof?p[3]:p[0]);
        if(f>=n.activate) {
          const levels=Math.max(1,Math.floor(n.height/.38));
          for(let l=0;
          l<levels;
          l++)for(let col=0;
          col<2;
          col++) {
            let on=n.activate+Math.min(l*2+col+((d.seed??0)%3+3)%3,N-6-n.activate);
            if(f>=on) {
              let a=project(n.x+.351,n.y-.18+col*.30,.2+l*.32);
              c.fillStyle=p[0];
              c.fillRect(a[0]-6,a[1]-7,12,14);
            }
          }
        }
        if(n.kind==='garden') {
          let g=project(n.x,n.y,z+.15);
          h.circle(c,g[0],g[1],24,p[3]);
        }
        let a=project(n.x,n.y);
        h.text(c,n.label,a[0]-105,a[1]+40,210,28, {
          color:p[1],minSize:24
        });
      });
      // Activated connections deliver a moving marker on a shared frame schedule.
      for(const q of d.flows??[]) {
        if(f<q.in||f>=q.out)continue;
        const a=d.nodes.find(n=>n.id===q.from),
        b=d.nodes.find(n=>n.id===q.to),
        A=project(a.x,a.y,.08),
        B=project(b.x,b.y,.08);
        c.strokeStyle=p[2];
        c.lineWidth=5;
        c.beginPath();
        c.moveTo(...A);
        c.lineTo(...B);
        c.stroke();
        let u=((f-q.in)%30)/30;
        h.circle(c,h.lerp(A[0],B[0],u),h.lerp(A[1],B[1],u),9,p[1]);
      }
      const legend=d.nodes.map(n=>(f>=n.activate?'● ':'○ ')+n.label).join('   ');
      h.text(c,legend,100,975,1720,28, {
        color:p[1],minSize:24
      });
    }
  };
})();
