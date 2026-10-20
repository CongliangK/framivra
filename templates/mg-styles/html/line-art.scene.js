(()=> {
  'use strict';
  const P=['#faf5e8',
  '#202730',
  '#e66b42',
  '#49a69d'];
  const recipe= {
    "编辑练习":"把最后一拍设为 hold=42、fill_frames=12、hero_hold=30；若延长填色，同步增加 hold 或总帧数，保留30帧满色阅读。",
    "适用/不适用":"概念演化与因果故事；不用于照片细节",
    "视觉隐喻":"一条线把概念接成结果",
    "4–6拍":["线头引入",
    "第一物体成形",
    "延伸到第二意义",
    "接成共同结果",
    "描边转实色并留读"],
    "不可删特征":"连续线头、按弧长揭示、最后实色英雄与阅读段",
    "可替语义":"物体语义、标签、拍窗、描速、fill_frames/hero_hold",
    "动作/转场/声音点":"线头抵达与实色完成可设音点；无实际音轨",
    "失败反例":"单纯替换图标；最后一帧才上色无法阅读"
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
    id:"line-art",
    title:"一笔叙事：从轮廓到共同结果",
    limits:"按语义路径重建的Canvas参考；非媒体路径像素等价副本。最后实色与阅读预算可换。无音轨。",
    examples:[ {
      "name":"种子到共同的家",
      "frames":240,
      "data": {
        "title":"从一粒种子到共同的家",
        "subtitle":"生长，阅读，共建。",
        "drawing_speed":1,
        "story":[ {
          "object":"seed",
          "label":"一粒种子",
          "in":0,
          "out":60,
          "hold":18
        },
        {
          "object":"tree",
          "label":"长成一棵树",
          "in":60,
          "out":120,
          "hold":18
        },
        {
          "object":"book",
          "label":"知识被传递",
          "in":120,
          "out":180,
          "hold":18
        },
        {
          "object":"house",
          "label":"一起建成家",
          "in":180,
          "out":240,
          "hold":42,
          "fill_frames":12,
          "hero_hold":30
        }]
      }
    },
    {
      "name":"知识跨过围墙",
      "frames":240,
      "data": {
        "title":"让知识跨过围墙",
        "subtitle":"从一间屋，走向更多的人。",
        "drawing_speed":1,
        "palette":["#f7f5ee",
        "#243b40",
        "#598077",
        "#c1d7be"],
        "story":[ {
          "object":"house",
          "label":"打开一间屋",
          "in":0,
          "out":80,
          "hold":24
        },
        {
          "object":"book",
          "label":"共享一本书",
          "in":80,
          "out":160,
          "hold":24
        },
        {
          "object":"network",
          "label":"连接更多的人",
          "in":160,
          "out":240,
          "hold":42,
          "fill_frames":12,
          "hero_hold":30
        }]
      }
    }],
    recipe,
    // Continuous semantic windows include explicit final fill and reading budgets.
    validate:(d,N,h)=> {
      common(d,N,h);
      h.int(d.drawing_speed??1,'drawing_speed',1,8);
      h.windows(d.story,N,2);
      if(d.story.length<2||d.story.length>4)throw Error('2..4 objects');
      d.story.forEach((s,i)=> {
        if(!['seed','tree','book','house','network'].includes(s.object))throw Error('object');
        h.label(s.label,'label',24);
        h.int(s.hold,'hold',1,s.out-s.in-1);
        if(i===d.story.length-1) {
          let available=s.out-s.in-Math.ceil((s.out-s.in-s.hold)/(d.drawing_speed??1));
          h.int(s.fill_frames??Math.min(24,available),'fill',1,available);
          h.int(s.hero_hold??0,'hero_hold',0,available-(s.fill_frames??Math.min(24,available)));
        }
      });
    },
    // Connect each semantic outline to the previous tip, then reveal by arc length.
    render:(c,d,f,N,h)=> {
      header(c,d,h);
      const p=d.palette??P,
      count=d.story.length,
      paths= {
        seed:[[-.5,
        0],
        [-.3,
        -.3],
        [0,
        -.4],
        [.4,
        -.15],
        [.5,
        .15],
        [.1,
        .4],
        [-.3,
        .3],
        [-.5,
        0]],
        tree:[[0,
        .6],
        [0,
        -.1],
        [-.5,
        -.1],
        [-.25,
        -.5],
        [0,
        -.8],
        [.25,
        -.5],
        [.5,
        -.1],
        [0,
        -.1]],
        book:[[-.6,
        -.5],
        [0,
        -.3],
        [.6,
        -.5],
        [.6,
        .5],
        [0,
        .3],
        [-.6,
        .5],
        [-.6,
        -.5]],
        house:[[-.6,
        .6],
        [-.6,
        -.2],
        [0,
        -.7],
        [.6,
        -.2],
        [.6,
        .6],
        [-.6,
        .6]],
        network:[[-.6,
        0],
        [0,
        -.5],
        [.6,
        0],
        [0,
        .5],
        [-.6,
        0],
        [.6,
        0]]
      };
      let previous=null;
      c.strokeStyle=p[1];
      c.lineWidth=7;
      c.lineCap='round';
      d.story.forEach((s,i)=> {
        let center=[160+(i+.5)*1600/count,580],pts=paths[s.object].map(a=>[center[0]+a[0]*190,center[1]+a[1]*190]);
        if(previous)pts=[previous,...pts];
        previous=pts[pts.length-1];
        if(f<s.in)return;
        let draw=Math.ceil((s.out-s.in-s.hold)/(d.drawing_speed??1)),u=h.clamp((f-s.in)/draw),lens=pts.slice(1).map((q,j)=>Math.hypot(q[0]-pts[j][0],q[1]-pts[j][1])),target=lens.reduce((a,b)=>a+b,0)*u;
        c.beginPath();
        c.moveTo(...pts[0]);
        for(let j=0;
        j<lens.length;
        j++) {
          let k=Math.min(1,target/lens[j]),a=pts[j],b=pts[j+1];
          c.lineTo(a[0]+(b[0]-a[0])*k,a[1]+(b[1]-a[1])*k);
          target-=lens[j];
          if(k<1)break;
        }
        c.stroke();
        h.text(c,s.label,center[0]-180,810,360,32, {
          color:p[1],minSize:24,align:'left'
        });
        // Only the final completed outline becomes the filled result.
        if(i===count-1&&f>=s.in+draw) {
          let fill=s.fill_frames??Math.min(24,s.out-s.in-draw);
          c.save();
          c.globalAlpha=h.clamp((f-s.in-draw+1)/fill);
          h.polygon(c,paths[s.object].map(a=>[center[0]+a[0]*190,center[1]+a[1]*190]),p[2]);
          c.restore();
        }
      });
    }
  };
})();
