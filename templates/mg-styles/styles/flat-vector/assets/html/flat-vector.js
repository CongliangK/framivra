(() => {
  'use strict';
  const {thinLine: line, labelText: txt, validateSeededTitle: base} = window.MGSceneHelpers;
  // 内容契约：因果边按卡片顺序构成无环图；揭示与重排都保留完整终态。
  function validate(d,N,h){
    base(d,N,h);
    if(d.palette!==undefined)h.palette(d.palette,6);
    h.label(d.subtitle,'subtitle',70);
    h.label(d.origin.label,'origin',24);
    h.label(d.origin.detail,'origin detail',40);
    if(!Array.isArray(d.cards)||d.cards.length<1||d.cards.length>6)throw Error('1..6 cards');
    const ids=['origin'];
    d.cards.forEach(x=>{
      h.label(x.id,'id',30);
      if(ids.includes(x.id))throw Error('duplicate id');
      ids.push(x.id);
      h.label(x.label,'label',24);
      h.label(x.detail,'detail',40);
      h.num(x.value,'value',-1e9,1e9);
    });
    if(!Array.isArray(d.links)||d.links.length>12)throw Error('links');
    d.links.forEach(l=>{
      if(!ids.includes(l.from)||!ids.includes(l.to)||l.to==='origin'||ids.indexOf(l.from)>=ids.indexOf(l.to))throw Error('links must follow acyclic card order');
    });
    if(!Array.isArray(d.final_order)||d.final_order.length!==d.cards.length||new Set(d.final_order).size!==d.cards.length||d.final_order.some(k=>!ids.slice(1).includes(k)))throw Error('final order');
    h.windows(d.stages,N,14);
    if(d.stages.map(s=>s.mode).join()!=='question,build,resolve')throw Error('stage order');
    d.stages.forEach(s=>h.label(s.text,'stage',60));
    if(d.stages[2].out-d.stages[2].in<31)throw Error('resolve needs 24-frame reflow plus settled hold');
    if(d.stages[1].out-d.stages[1].in<7*d.cards.length+27)throw Error('build needs full reveal plus six readable frames');
  }
  // 沿折线路径长度揭示箭头，避免把拐角当作新的节拍。
  function partialRoute(pts,q){
    const lengths=pts.slice(1).map((p,i)=>Math.hypot(p[0]-pts[i][0],p[1]-pts[i][1]));
    let remaining=lengths.reduce((a,b)=>a+b,0)*q,
    out=[pts[0]];
    for(let i=0;i<lengths.length;i++){
      if(remaining>=lengths[i]){
        out.push(pts[i+1]);
        remaining-=lengths[i];
      }else{
        const t=remaining/Math.max(.001,lengths[i]);
        out.push([pts[i][0]+(pts[i+1][0]-pts[i][0])*t,
        pts[i][1]+(pts[i+1][1]-pts[i][1])*t]);
        break;
      }
    }return out;
  }
  // 问题 → 节点建立 → 因果路径 → 卡片重排；所有状态仅由整数帧推导。
  function render(c,d,f,N,h){
    const colors=d.palette||['#f8f4e9', '#17243b', '#ef6650', '#3c8e86', '#ffffff', '#3c8e86'];
    c.fillStyle=colors[0];
    c.fillRect(0,0,1920,1080);
    const s=h.stage(d.stages,f),
    b=d.stages[1],
    local=Math.min(f-b.in,b.out-b.in-1),
    re=s.mode==='resolve'?h.smooth((f-s.in)/24):0,
    n=d.cards.length,
    cols=Math.min(3,n),
    rows=Math.ceil(n/cols),
    originMove=h.ease(Math.max(0,local)/12),
    originX=h.lerp(960,290,originMove),
    originY=h.lerp(590,600,originMove),
    pos={origin:[originX,originY]};
    d.cards.forEach((a,i)=>{
      const j=d.final_order.indexOf(a.id);
      pos[a.id]=[h.lerp(740+i%cols*420,960+(j%cols-(cols-1)/2)*540,re),
      h.lerp(rows===2?470+Math.floor(i/cols)*290:600,rows===2?470+Math.floor(j/cols)*290:600,re)];
    });
    txt(c,h,d.title,120,65,1680,64,colors[1]);
    txt(c,h,d.subtitle,120,155,1680,32,colors[1]);
    txt(c,h,s.text,120,235,1680,36,colors[3]);
    if(s.mode==='question'){
      h.circle(c,960,590,145,colors[2]);
      txt(c,h,d.origin.label,800,555,320,46,'#ffffff');
      return;
    }
    // 重排开始后收起连线；静态卡片之间沿开放 gutter 分配独立 lane。
    if(re===0)d.links.forEach((l,i)=>{
      const [sx, sy]=pos[l.from],
      [tx, ty]=pos[l.to],
      sg=sx+(l.from==='origin'?140:210),
      lane=292+i*5,
      tg=tx-210;
      const targetIndex=d.cards.findIndex(a=>a.id===l.to),
      q=h.clamp((local-12-targetIndex*7)/16);
      if(q<=0)return;
      line(c,partialRoute([[sx+(l.from==='origin'?118:190), sy],
      [sg, sy],
      [sg, lane],
      [tg, lane],
      [tg, ty],
      [tx-190, ty]],q),colors[3],4);
      if(q===1)h.polygon(c,[[tx-190, ty],
      [tx-204, ty-8],
      [tx-204, ty+8]],colors[3]);
    });
    if(!re){
      h.circle(c,originX,originY,h.lerp(145,118,originMove),colors[2]);
      txt(c,h,d.origin.label,h.lerp(800,190,originMove),h.lerp(555,568,originMove),h.lerp(320,200,originMove),h.lerp(46,32,originMove),'#ffffff');
    }d.cards.forEach((a,i)=>{
      const q=h.clamp((local-12-i*7)/16);
      if(q<=0)return;
      const [x, y]=pos[a.id];
      c.save();
      c.translate(x,y+40*(1-h.ease(q)));
      const z=h.ease(q)+Math.sin(q*Math.PI)*.08;
      c.scale(z,z);
      c.fillStyle=colors[4];
      c.fillRect(-190,-108,380,215);
      c.strokeStyle=colors[5];
      c.strokeRect(-190,-108,380,215);
      if(q===1){
        txt(c,h,a.label,-165,-85,330,34,colors[1]);
        txt(c,h,a.value,-165,-35,330,46,colors[2]);
        txt(c,h,a.detail,-165,38,330,26,colors[1]);
      }c.restore();
    });
  } window.MG_REFERENCE={
    id:"flat-vector",
    title:"扁平信息图",
    limits:"程序图形参考；浏览器字体与成片质量仍需独立视觉验收。无内置声音。",
    examples:[{
      "name": "把一个想法，变成可用产品",
      "frames": 180,
      "data": {
        "title": "把一个想法，变成可用产品",
        "subtitle": "先验证问题，再构建方案，最后交付真实价值",
        "origin": {
          "label": "一个想法",
          "detail": "从用户的问题出发"
        },
        "cards": [{
          "id": "observe",
          "label": "观察用户",
          "value": 12,
          "detail": "真实访谈，不猜需求"
        },
        {
          "id": "prototype",
          "label": "制作原型",
          "value": 3,
          "detail": "快速检验关键假设"
        },
        {
          "id": "deliver",
          "label": "交付改进",
          "value": 1,
          "detail": "用真实反馈推动下一轮"
        }],
        "links": [{
          "from": "origin",
          "to": "observe"
        },
        {
          "from": "observe",
          "to": "prototype"
        },
        {
          "from": "prototype",
          "to": "deliver"
        }],
        "final_order": ["observe", "prototype", "deliver"],
        "stages": [{
          "mode": "question",
          "in": 0,
          "out": 36,
          "text": "好想法，怎样成为好产品？"
        },
        {
          "mode": "build",
          "in": 36,
          "out": 108,
          "text": "观察带来证据，原型连接行动"
        },
        {
          "mode": "resolve",
          "in": 108,
          "out": 180,
          "text": "把因果链整理成一条可执行路径"
        }],
        "seed": -12
      }
    },
    {
      "name": "一栋楼的节能行动",
      "frames": 240,
      "data": {
        "title": "一栋楼的节能行动",
        "subtitle": "把发现、行动与结果重新排列成清楚的计划",
        "origin": {
          "label": "发现浪费",
          "detail": "同一个目标，两条改进路径"
        },
        "cards": [{
          "id": "light",
          "label": "照明排查",
          "value": 48,
          "detail": "找到无人在场的亮灯区"
        },
        {
          "id": "water",
          "label": "用水排查",
          "value": 16,
          "detail": "记录持续滴漏的接头"
        },
        {
          "id": "sensor",
          "label": "安装感应",
          "value": 24,
          "detail": "让灯光跟随实际使用"
        },
        {
          "id": "repair",
          "label": "修复漏点",
          "value": 12,
          "detail": "逐一消除不必要消耗"
        },
        {
          "id": "power",
          "label": "电量下降",
          "value": 18,
          "detail": "对比相同时段用量"
        },
        {
          "id": "saving",
          "label": "用水下降",
          "value": 22,
          "detail": "观察实际节约结果"
        }],
        "links": [{
          "from": "origin",
          "to": "light"
        },
        {
          "from": "origin",
          "to": "water"
        },
        {
          "from": "light",
          "to": "sensor"
        },
        {
          "from": "water",
          "to": "repair"
        },
        {
          "from": "sensor",
          "to": "power"
        },
        {
          "from": "repair",
          "to": "saving"
        }],
        "final_order": ["light", "sensor", "power", "water", "repair", "saving"],
        "stages": [{
          "mode": "question",
          "in": 0,
          "out": 45,
          "text": "浪费从哪里来？先观察，再行动"
        },
        {
          "mode": "build",
          "in": 45,
          "out": 150,
          "text": "两条原因链，各自连接对应行动"
        },
        {
          "mode": "resolve",
          "in": 150,
          "out": 240,
          "text": "重排为：发现 → 行动 → 结果"
        }],
        "palette": ["#f5f1e8", "#1e3034", "#f1775c", "#76c5aa", "#f8c653", "#95b5e6"],
        "seed": 27
      }
    }],
    recipe:{
      "适用/不适用": "适用解释可追溯因果与执行顺序；不适用无关系的装饰卡片。",
      "视觉隐喻": "问题生长为证据节点，再整理为行动路径。",
      "4–6拍": ["提出问题", "原点预备", "逐个建立证据", "沿因果线展开", "卡片重排", "结论停留"],
      "不可删特征": "清楚信息层级、节点完整揭示、避开卡片的因果边、最终重排。",
      "可替语义": "问题、节点标签/数值/解释、链接与最终次序、三阶段整数窗。",
      "编辑练习": "把三节点链改为分叉，调整build窗口并检查全部节点揭示后留读。",
      "动作/转场/声音点": "卡片预备后过冲落定；连线完成和重排落定可绑定独立声音 cue；本页无音轨。",
      "失败反例": "连线穿过兄弟卡、文字未完成就冻结、每张卡同时摇晃。"
    },
    validate,
    render
  };
})();
