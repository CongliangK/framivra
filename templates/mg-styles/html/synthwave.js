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
  // 内容契约：启程/穿越/抵达覆盖全片；短扰动必须在抵达前结束。
  function validate(d,N,h){
    base(d,N,h);
    h.label(d.destination,'destination',40);
    h.windows(d.stages,N,20);
    if(d.stages.map(s=>s.mode).join()!=='approach,cross,arrive')throw Error('stage order');
    if(!Array.isArray(d.waypoints)||d.waypoints.length>10)throw Error('waypoints');
    d.waypoints.forEach(p=>{
      h.int(p.frame,'waypoint',0,d.stages[2].in-1);
      h.label(p.label,'waypoint label',20);
      if(!['left', 'right'].includes(p.side))throw Error('side');
    });
    if(!Array.isArray(d.tracking))throw Error('tracking');
    d.tracking.forEach(e=>{
      h.int(e.in,'tracking in',0,d.stages[2].in-1);
      h.int(e.out,'tracking out',e.in+1,d.stages[2].in);
      h.int(e.strength,'strength',1,20);
    });
  }
  // 透视旅程：抵达缓动到停止；山脉采样世界索引而非循环屏幕索引。
  function render(c,d,f,N,h){
    const arrival=d.stages[2].in,
    stop=N-11,
    t=f<arrival?f:arrival+(stop-arrival)*h.ease((f-arrival)/Math.max(1,stop-arrival)),
    pink='#ff58ca',
    cyan='#54e6f2';
    c.fillStyle='#140b2d';
    c.fillRect(0,0,1920,1080);
    for(let y=310;y<650;y+=4){
      if(y>500&&y%22>=13)continue;
      const dy=y-480;
      if(Math.abs(dy)<170){
        const dx=Math.sqrt(170**2-dy**2);
        line(c,[[960-dx, y],
        [960+dx, y]],'#ffad68',4);
      }
    }for(let layer=0;layer<2;layer++){
      const shift=t*(.25+.35*layer),
      first=Math.floor(shift/80)-2,
      pts=[[0, 580]];
      for(let i=first;i<first+30;i++)pts.push([i*80-shift, 500-60*layer-65*Math.sin(i*80*.006+d.seed+layer)]);
      pts.push([1920, 580]);
      h.polygon(c,pts,layer?'#281234':'#1b102d');
    }c.fillStyle='#0b081f';
    c.fillRect(0,580,1920,500);
    for(let i=-12;i<=12;i++)line(c,[[960, 580],
    [960+i*190, 1080]],cyan,2);
    const phase=t*.013%1;
    for(let i=1;i<22;i++){
      const y=580+500*((i-phase)/22)**2;
      line(c,[[0, y],
      [1920, y]],pink,2);
    }
    // 路标持续投影到完整离屏；先做保守边界拒绝，避免近面奇点。
    d.waypoints.forEach(p=>{
      const den=1+(p.frame-t)/45;
      if(den<=0)return;
      const scale=1/den,
      x=960+(p.side==='left'?-620:620)*scale,
      w=140*scale,
      y=580+330*scale,
      z=210*scale;
      if(x+Math.max(w,240)<-64||x-Math.max(w,240)>1984)return;
      c.save();
      c.beginPath();
      c.rect(0,0,1920,1080);
      c.clip();
      c.shadowColor=cyan;
      c.shadowBlur=12;
      line(c,[[x-w, y],
      [x-w, y-z],
      [x+w, y-z],
      [x+w, y]],cyan,4);
      line(c,[[x-w, y-z*.65],
      [x+w, y-z*.65]],pink,3);
      txt(c,h,p.label,x-200,y-z-60,400,Math.min(60,25+14*scale),cyan);
      c.restore();
    });
    c.save();
    c.beginPath();
    c.rect(0,0,1920*h.clamp(f/Math.max(1,d.stages[0].out-1)),300);
    c.clip();
    txt(c,h,d.title,140,100,1640,116,cyan);
    c.restore();
    if(f>=arrival){
      c.fillStyle='#100d25';
      c.fillRect(170,810,1580,200);
      txt(c,h,d.destination,220,870,1480,72,cyan);
    }for(let y=0;y<1080;y+=4){
      c.fillStyle='#08061622';
      c.fillRect(0,y,1920,1);
    }d.tracking.forEach(e=>{
      if(e.in<=f&&f<e.out){
        const y=280+(f*37+d.seed)%420;
        c.fillStyle='#ff58ca55';
        c.fillRect(0,y,1920,Math.min(12,e.strength));
      }
    });
  } window.MG_REFERENCE={
    id:"synthwave",
    title:"合成波旅程",
    limits:"程序图形参考；浏览器字体与成片质量仍需独立视觉验收。无内置声音。",
    examples:[{
      "name": "穿越夜色",
      "frames": 180,
      "data": {
        "title": "穿越夜色",
        "destination": "抵达新的灵感站",
        "seed": 21,
        "waypoints": [{
          "frame": 24,
          "label": "起点",
          "side": "left"
        },
        {
          "frame": 72,
          "label": "光桥",
          "side": "right"
        }],
        "stages": [{
          "mode": "approach",
          "in": 0,
          "out": 60
        },
        {
          "mode": "cross",
          "in": 60,
          "out": 120
        },
        {
          "mode": "arrive",
          "in": 120,
          "out": 180
        }],
        "tracking": [{ "in": 65, "out": 70, "strength": 7 }]
      }
    },
    {
      "name": "向海边出发",
      "frames": 240,
      "data": {
        "title": "向海边出发",
        "destination": "沿途每一束光都是坐标",
        "seed": -9,
        "palette": ["#0e1738", "#f65b9d", "#48efdd", "#ffd081"],
        "waypoints": [{
          "frame": 25,
          "label": "城市边缘",
          "side": "right"
        },
        {
          "frame": 65,
          "label": "山间电台",
          "side": "left"
        },
        {
          "frame": 105,
          "label": "海风隧道",
          "side": "right"
        },
        {
          "frame": 150,
          "label": "灯塔",
          "side": "left"
        }],
        "stages": [{
          "mode": "approach",
          "in": 0,
          "out": 80
        },
        {
          "mode": "cross",
          "in": 80,
          "out": 180
        },
        {
          "mode": "arrive",
          "in": 180,
          "out": 240
        }],
        "tracking": [{
          "in": 90,
          "out": 96,
          "strength": 10
        },
        {
          "in": 150,
          "out": 154,
          "strength": 5
        }]
      }
    }],
    recipe:{
      "适用/不适用": "适用启程、穿越、抵达的空间叙事；不适用霓虹滤镜套任意信息。",
      "视觉隐喻": "透视网格把未知远方变为可以抵达的目的地。",
      "4–6拍": ["标题揭示", "远景展开", "经过路标", "短追踪扰动", "逐渐减速", "抵达停留"],
      "不可删特征": "固定消失点、分层视差、连续世界地形、路标完整离屏、抵达静止。",
      "可替语义": "标题、目的地、路标时间/方向、阶段与短扰动事件。",
      "编辑练习": "增加一个路标，调整cross与arrive窗口，保证路标完整离屏及抵达停留。",
      "动作/转场/声音点": "路标经过、穿越、到达可配独立 cue；本页无音轨。",
      "失败反例": "路标仍可见却整块消失、山脉周期跳切、结尾持续漂移。"
    },
    validate,
    render
  };
})();
