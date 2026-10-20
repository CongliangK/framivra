(() => {
  'use strict';
  const line=(c,pts,color,width=3)=>{
    c.strokeStyle=color;
    c.lineWidth=width;
    c.beginPath();
    pts.forEach(([x, y],i)=>i?c.lineTo(x,y):c.moveTo(x,y));
    c.stroke();
  };
  const txt=(c,h,s,x,y,w,size=36,color='#18243b')=>h.text(c,String(s),x,y,w,size,{
    color,
    minSize:24,
    height:size*1.3
  });
  const base=(d,N,h)=>{
    h.int(N,'frames',30,18000);
    h.int(d.seed,'seed',-2147483648,2147483647);
    h.label(d.title,'title',40);
  };
  // cell 是模块包围方格的左上格；网格、跨度和落定位置共用同一模数。
  const GRID={x:300,y:380,unit:100,columns:11,rows:6};
  function modulePose(d,beat,index,m,rank,local,h){
    const old=d.layouts[index?d.beats[index-1].target:d.initial_layout].find(v=>v.id===m.id);
    const next=d.layouts[beat.target].find(v=>v.id===m.id);
    const tick=Math.floor(local/d.tick_frames)*d.tick_frames;
    const u=h.ease((tick-rank*beat.cascade_frames)/beat.move_frames),z=m.span*GRID.unit;
    // 选择最短四分之一圈路径；旋转轴随布局平移，端点方格重新对齐共同网格。
    const delta=((next.turn-old.turn+6)%4)-2;
    const a=old.turn*Math.PI/2,b=a+delta*Math.PI/2,angle=h.lerp(a,b,u);
    const corner=beat.axis==='corner'||beat.axis==='alternate'&&rank%2;
    let x=GRID.x+(h.lerp(old.cell[0],next.cell[0],u)+m.span/2)*GRID.unit;
    let y=GRID.y+(h.lerp(old.cell[1],next.cell[1],u)+m.span/2)*GRID.unit;
    if(corner){
      const offset=t=>[(Math.cos(t)-Math.sin(t)-1)*z/2,(Math.sin(t)+Math.cos(t)-1)*z/2];
      const from=offset(a),to=offset(b);
      x-=h.lerp(from[0],to[0],u);y-=h.lerp(from[1],to[1],u);
    }
    return {x,y,z,angle,corner};
  }
  function poseCorners(p){
    return [[-1,-1],[1,-1],[1,1],[-1,1]].map(([sx,sy])=>{
      let x=sx*p.z/2,y=sy*p.z/2;
      if(p.corner){x+=p.z/2;y+=p.z/2;}
      const rx=x*Math.cos(p.angle)-y*Math.sin(p.angle),ry=x*Math.sin(p.angle)+y*Math.cos(p.angle);
      return [p.x+rx-(p.corner?p.z/2:0),p.y+ry-(p.corner?p.z/2:0)];
    });
  }
  // 内容契约：有限模块、共同模数、具名布局和明确旋转轴；每拍留出落定阅读。
  function validate(d,N,h){
    base(d,N,h);
    h.label(d.subtitle,'subtitle',70);
    h.palette(d.palette,5);
    h.int(d.tick_frames,'tick',1,15);
    if(!Array.isArray(d.modules)||!d.modules.length||d.modules.length>8)throw Error('modules');
    const ids=new Set();
    d.modules.forEach(m=>{
      h.label(m.id,'id',30);
      if(ids.has(m.id))throw Error('duplicate module');
      ids.add(m.id);
      if(!['circle', 'quarter', 'triangle', 'bar', 'semicircle', 'square'].includes(m.shape))throw Error('shape');
      h.int(m.span,'span',1,3);
      h.int(m.color,'color',1,4);
    });
    for(const rows of Object.values(d.layouts)){
      if(rows.length!==ids.size||new Set(rows.map(r=>r.id)).size!==ids.size)throw Error('layout coverage');
      rows.forEach(r=>{
        if(!ids.has(r.id))throw Error('layout id');
        h.int(r.cell[0],'column',0,9);
        h.int(r.cell[1],'row',0,4);
        h.int(r.turn,'turn',0,3);
        const m=d.modules.find(m=>m.id===r.id);
        if(r.cell[0]+m.span>11||r.cell[1]+m.span>6)throw Error('module outside grid');
      });
    }if(!d.layouts[d.initial_layout])throw Error('initial layout');
    h.windows(d.beats,N,18);
    d.beats.forEach(b=>{
      h.label(b.label,'beat',40);
      if(!d.layouts[b.target]||!['center', 'corner', 'alternate'].includes(b.axis))throw Error('target/axis');
      h.int(b.move_frames,'move',1,40);
      h.int(b.cascade_frames,'cascade',0,15);
      const settled=Math.ceil((b.move_frames+(d.modules.length-1)*b.cascade_frames)/d.tick_frames)*d.tick_frames;
      if(settled+6>b.out-b.in)throw Error('beat needs quantized landing plus six hold frames');
      const index=d.beats.indexOf(b);
      for(let local=0;local<b.out-b.in;local++)d.modules.forEach((m,rank)=>{
        const pts=poseCorners(modulePose(d,b,index,m,rank,local,h));
        if(pts.some(([x,y])=>x<180-1e-6||x>1740+1e-6||y<310-1e-6||y>1040+1e-6))throw Error('rotated module leaves scene safe region');
      });
    });
  }
  // 模数 tick 量化帧相位；级联起动、轴心旋转和布局插值共同形成秩序。
  function render(c,d,f,N,h){
    const p=d.palette,
    s=h.stage(d.beats,f),
    index=d.beats.indexOf(s);
    c.fillStyle=p[0];
    c.fillRect(0,0,1920,1080);
    txt(c,h,d.title,110,65,1700,64,p[1]);
    txt(c,h,d.subtitle,110,155,1700,30,p[1]);
    txt(c,h,s.label,110,250,1700,34,p[1]);
    c.strokeStyle='#d3c9b8';
    for(let column=0;column<=GRID.columns;column++){
      const x=GRID.x+column*GRID.unit;
      line(c,[[x,GRID.y],[x,GRID.y+GRID.rows*GRID.unit]],'#d3c9b8',1);
    }
    for(let row=0;row<=GRID.rows;row++){
      const y=GRID.y+row*GRID.unit;
      line(c,[[GRID.x,y],[GRID.x+GRID.columns*GRID.unit,y]],'#d3c9b8',1);
    }
    // 同一个模块从上一布局移动到当前布局，避免换镜头时凭空替换形状。
    d.modules.forEach((m,i)=>{
      const {x,y,z,angle,corner}=modulePose(d,s,index,m,i,f-s.in,h);
      c.save();
      c.translate(x,y);
      if(corner)c.translate(-z/2,-z/2);
      c.rotate(angle);
      if(corner)c.translate(z/2,z/2);
      c.fillStyle=p[m.color];
      if(m.shape==='circle')h.circle(c,0,0,z/2,p[m.color]);
      else if(m.shape==='triangle')h.polygon(c,[[-z/2, z/2],
      [z/2, z/2],
      [0, -z/2]],p[m.color]);
      else if(m.shape==='quarter'||m.shape==='semicircle'){
        c.beginPath();
        c.moveTo(0,0);
        c.arc(0,0,z/2,0,m.shape==='quarter'?Math.PI/2:Math.PI);
        c.closePath();
        c.fill();
      }else c.fillRect(-z/2,-z/2,z,m.shape==='bar'?z/3:z);
      c.restore();
    });
  } window.MG_REFERENCE={
    id:"bauhaus",
    title:"包豪斯模块构成",
    limits:"程序图形参考；浏览器字体与成片质量仍需独立视觉验收。无内置声音。",
    examples:[{
      "name": "让形状成为秩序",
      "frames": 180,
      "data": {
        "title": "让形状成为秩序",
        "subtitle": "一个模数，三种轴心，共同的节奏。",
        "seed": -7,
        "palette": ["#f1e9da", "#111111", "#df3b30", "#f2b705", "#2454a5"],
        "tick_frames": 5,
        "modules": [{
          "id": "disc",
          "shape": "circle",
          "span": 3,
          "color": 2
        },
        {
          "id": "arc",
          "shape": "quarter",
          "span": 2,
          "color": 4
        },
        {
          "id": "direction",
          "shape": "triangle",
          "span": 1,
          "color": 3
        },
        {
          "id": "beam",
          "shape": "bar",
          "span": 2,
          "color": 1
        },
        {
          "id": "half",
          "shape": "semicircle",
          "span": 2,
          "color": 3
        },
        {
          "id": "anchor",
          "shape": "square",
          "span": 1,
          "color": 4
        }],
        "initial_layout": "loose",
        "layouts": {
          "loose": [{
            "id": "disc",
            "cell": [2, 1],
            "turn": 0
          },
          {
            "id": "arc",
            "cell": [7, 0],
            "turn": 0
          },
          {
            "id": "direction",
            "cell": [8, 3],
            "turn": 0
          },
          {
            "id": "beam",
            "cell": [8, 2],
            "turn": 0
          },
          {
            "id": "half",
            "cell": [5, 3],
            "turn": 2
          },
          {
            "id": "anchor",
            "cell": [4, 0],
            "turn": 0
          }],
          "focus": [{
            "id": "disc",
            "cell": [3, 1],
            "turn": 0
          },
          {
            "id": "arc",
            "cell": [6, 1],
            "turn": 1
          },
          {
            "id": "direction",
            "cell": [7, 2],
            "turn": 1
          },
          {
            "id": "beam",
            "cell": [8, 1],
            "turn": 1
          },
          {
            "id": "half",
            "cell": [5, 2],
            "turn": 3
          },
          {
            "id": "anchor",
            "cell": [5, 1],
            "turn": 1
          }],
          "rhythm": [{
            "id": "disc",
            "cell": [3, 1],
            "turn": 0
          },
          {
            "id": "arc",
            "cell": [5, 0],
            "turn": 2
          },
          {
            "id": "direction",
            "cell": [6, 2],
            "turn": 2
          },
          {
            "id": "beam",
            "cell": [8, 2],
            "turn": 2
          },
          {
            "id": "half",
            "cell": [5, 2],
            "turn": 0
          },
          {
            "id": "anchor",
            "cell": [6, 1],
            "turn": 2
          }],
          "poster": [{
            "id": "disc",
            "cell": [3, 1],
            "turn": 0
          },
          {
            "id": "arc",
            "cell": [6, 1],
            "turn": 3
          },
          {
            "id": "direction",
            "cell": [7, 3],
            "turn": 3
          },
          {
            "id": "beam",
            "cell": [8, 1],
            "turn": 3
          },
          {
            "id": "half",
            "cell": [6, 2],
            "turn": 1
          },
          {
            "id": "anchor",
            "cell": [5, 2],
            "turn": 3
          }]
        },
        "beats": [{
          "in": 0,
          "out": 60,
          "label": "01  找到共同模数",
          "target": "focus",
          "axis": "center",
          "move_frames": 10,
          "cascade_frames": 5
        },
        {
          "in": 60,
          "out": 120,
          "label": "02  改变旋转轴心",
          "target": "rhythm",
          "axis": "corner",
          "move_frames": 10,
          "cascade_frames": 5
        },
        {
          "in": 120,
          "out": 180,
          "label": "03  级联形成整体",
          "target": "poster",
          "axis": "alternate",
          "move_frames": 10,
          "cascade_frames": 5
        }]
      }
    },
    {
      "name": "构成一种平衡",
      "frames": 180,
      "data": {
        "title": "构成一种平衡",
        "subtitle": "圆弧给出方向，支撑建立联结。",
        "seed": 2147483647,
        "palette": ["#f1e9da", "#111111", "#df3b30", "#f2b705", "#2454a5"],
        "tick_frames": 5,
        "modules": [{
          "id": "shelter",
          "shape": "quarter",
          "span": 3,
          "color": 4
        },
        {
          "id": "sun",
          "shape": "circle",
          "span": 2,
          "color": 3
        },
        {
          "id": "bridge",
          "shape": "bar",
          "span": 2,
          "color": 2
        },
        {
          "id": "arrow",
          "shape": "triangle",
          "span": 1,
          "color": 1
        }],
        "initial_layout": "apart",
        "layouts": {
          "apart": [{
            "id": "shelter",
            "cell": [3, 1],
            "turn": 0
          },
          {
            "id": "sun",
            "cell": [7, 1],
            "turn": 0
          },
          {
            "id": "bridge",
            "cell": [5, 3],
            "turn": 0
          },
          {
            "id": "arrow",
            "cell": [8, 0],
            "turn": 0
          }],
          "align": [{
            "id": "shelter",
            "cell": [4, 1],
            "turn": 1
          },
          {
            "id": "sun",
            "cell": [7, 0],
            "turn": 1
          },
          {
            "id": "bridge",
            "cell": [6, 1],
            "turn": 1
          },
          {
            "id": "arrow",
            "cell": [4, 3],
            "turn": 1
          }],
          "counterpoint": [{
            "id": "shelter",
            "cell": [3, 1],
            "turn": 2
          },
          {
            "id": "sun",
            "cell": [6, 2],
            "turn": 2
          },
          {
            "id": "bridge",
            "cell": [5, 1],
            "turn": 2
          },
          {
            "id": "arrow",
            "cell": [7, 3],
            "turn": 2
          }],
          "balance": [{
            "id": "shelter",
            "cell": [4, 1],
            "turn": 3
          },
          {
            "id": "sun",
            "cell": [6, 1],
            "turn": 3
          },
          {
            "id": "bridge",
            "cell": [6, 2],
            "turn": 3
          },
          {
            "id": "arrow",
            "cell": [3, 2],
            "turn": 3
          }]
        },
        "beats": [{
          "in": 0,
          "out": 60,
          "label": "01  分离的形状",
          "target": "align",
          "axis": "center",
          "move_frames": 10,
          "cascade_frames": 5
        },
        {
          "in": 60,
          "out": 120,
          "label": "02  轴心产生呼应",
          "target": "counterpoint",
          "axis": "corner",
          "move_frames": 10,
          "cascade_frames": 5
        },
        {
          "in": 120,
          "out": 180,
          "label": "03  共同形成平衡",
          "target": "balance",
          "axis": "alternate",
          "move_frames": 10,
          "cascade_frames": 5
        }]
      }
    }],
    recipe:{
      "适用/不适用": "适用模数、秩序、结构关系；不适用用几何装饰代替内容。",
      "视觉隐喻": "各自独立的基本形经轴心与模数形成共同秩序。",
      "4–6拍": ["建立共同模数", "逐个引入模块", "绕轴转向", "级联重组", "形成海报", "停留阅读"],
      "不可删特征": "有限原色、统一网格、圆弧与直线、明确旋转轴、级联留读。",
      "可替语义": "模块形状、跨度、布局、轴心、章节标签与节奏。",
      "编辑练习": "单模块设tick15、move31，章节至少51帧，包含量化落定后的6帧停留。",
      "动作/转场/声音点": "离散模数 tick 与每个模块落位可标声音点；本页无音轨。",
      "失败反例": "无模数的漂浮、轴心每帧跳变、形状之间没有秩序变化。"
    },
    validate,
    render
  };
})();
