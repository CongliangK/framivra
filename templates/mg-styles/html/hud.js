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
  // 内容契约：连续轨迹覆盖全片，搜索/发现/确认是可换状态字的整数窗口。
  function validate(d,N,h){
    base(d,N,h);
    h.label(d.subtitle,'subtitle',70);
    h.label(d.target.name,'target',32);
    const p=d.target.points;
    if(!Array.isArray(p)||p.length<2||p.length>32)throw Error('trajectory');
    let prev=-1;
    p.forEach(a=>{
      h.int(a.frame,'point frame',prev+1,N-1);
      prev=a.frame;
      h.num(a.x,'x',.30,.70);
      h.num(a.y,'y',.32,.72);
    });
    if(p[0].frame!==0||p.at(-1).frame!==N-1)throw Error('trajectory endpoints');
    h.windows(d.stages,N,18);
    if(d.stages.map(s=>s.mode).join()!=='search,detect,confirm')throw Error('stage order');
    d.stages.forEach(s=>h.label(s.text,'status',48));
    if(!Array.isArray(d.telemetry)||d.telemetry.length<1||d.telemetry.length>6)throw Error('1..6 telemetry');
    d.telemetry.forEach(a=>{
      h.label(a.label,'label',24);
      h.label(a.unit,'unit',12);
      h.num(a.value,'value',-1e9,1e9);
    });
  }
  // 轨迹与括号共享实际目标锚点；遥测终值来自输入，不把噪声当测量。
  function render(c,d,f,N,h){
    c.fillStyle='#071723';
    c.fillRect(0,0,1920,1080);
    const cyan='#50e7d4',
    dim='#174451',
    s=h.stage(d.stages,f),
    confirm=d.stages[2].in,
    settle=h.clamp((f-confirm)/Math.max(1,Math.min(20,N-confirm-11)));
    let a=d.target.points[0],
    b=a;
    for(let i=1;i<d.target.points.length;i++){
      b=d.target.points[i];
      if(f<=b.frame)break;
      a=b;
    }const u=h.smooth((f-a.frame)/Math.max(1,b.frame-a.frame)),
    x=1920*h.lerp(a.x,b.x,u),
    y=1080*h.lerp(a.y,b.y,u),
    lock=s.mode==='confirm'?1:s.mode==='detect'?h.clamp((f-s.in)/Math.max(1,s.out-s.in-1)):0,
    r=44+(Math.min(129,x-500,1420-x)-44)*(1-lock),
    nameY=Math.max(885,Math.ceil(y+153)),
    statusY=Math.max(974,nameY+50);
    c.strokeStyle=dim;
    for(let yy=330;yy<850;yy+=52)for(let xx=520;xx<1430;xx+=60){
      const pts=Array.from({
        length:6
      },(_,i)=>[xx+29*Math.cos(i*Math.PI/3), yy+29*Math.sin(i*Math.PI/3)]);
      h.polygon(c,pts);
    }txt(c,h,d.title,100,65,1720,58,cyan);
    txt(c,h,d.subtitle,100,150,1720,30,'#a2bec8');
    if(s.mode!=='confirm'){
      const scan=500+(f%90)/89*920;
      line(c,[[scan, 310],
      [scan, 850]],cyan,2);
    }
    // 分层发光描边仍使用同一锚点，最大框与名称安全带分开。
    for(const glow of [16, 7, 2]){
      c.save();
      c.globalAlpha=glow===2?1:.13;
      c.lineWidth=glow;
      c.strokeStyle=cyan;
      for(const dx of [-1, 1])for(const dy of [-1, 1])line(c,[[x+dx*r, y+dy*(r-22)],
      [x+dx*r, y+dy*r],
      [x+dx*(r-22), y+dy*r]],cyan,glow);
      c.restore();
    }h.circle(c,x,y,6,cyan);
    txt(c,h,d.target.name,530,nameY,860,34,cyan);
    txt(c,h,s.text,530,statusY,1250,32,cyan);
    d.telemetry.forEach((a,i)=>{
      const y=310+i*92,
      noise=settle===1?0:Math.sin((f+d.seed+i*17)*.23)*(1-settle)*Math.max(1,Math.abs(a.value)*.09),
      v=settle===1?a.value:Number((a.value+noise).toFixed(2));
      txt(c,h,a.label,100,y,350,26,'#a2bec8');
      txt(c,h,`${v} ${a.unit}`,100,y+36,350,32,cyan);
    });
  } window.MG_REFERENCE={
    id:"hud",
    title:"战术信息界面",
    limits:"程序图形参考；浏览器字体与成片质量仍需独立视觉验收。无内置声音。",
    examples:[{
      "name": "山地救援 · 信号追踪",
      "frames": 180,
      "data": {
        "title": "山地救援 · 信号追踪",
        "subtitle": "从搜索区域到确认坐标，逐步缩小不确定性",
        "target": {
          "name": "救援信标 07",
          "points": [{
            "frame": 0,
            "x": 0.34,
            "y": 0.4
          },
          {
            "frame": 50,
            "x": 0.6,
            "y": 0.6
          },
          {
            "frame": 105,
            "x": 0.55,
            "y": 0.48
          },
          {
            "frame": 179,
            "x": 0.55,
            "y": 0.48
          }]
        },
        "stages": [{
          "mode": "search",
          "in": 0,
          "out": 60,
          "text": "搜索山谷信标"
        },
        {
          "mode": "detect",
          "in": 60,
          "out": 125,
          "text": "发现信号 · 正在定位"
        },
        {
          "mode": "confirm",
          "in": 125,
          "out": 180,
          "text": "坐标确认 · 救援队出发"
        }],
        "telemetry": [{
          "label": "信号强度",
          "value": -67,
          "unit": "dBm"
        },
        {
          "label": "海拔",
          "value": 2186,
          "unit": "m"
        },
        {
          "label": "距救援点",
          "value": 1.8,
          "unit": "km"
        },
        {
          "label": "定位置信度",
          "value": 98.4,
          "unit": "%"
        }],
        "seed": -19
      }
    },
    {
      "name": "水下巡检 · 异常识别",
      "frames": 240,
      "data": {
        "title": "水下巡检 · 异常识别",
        "subtitle": "沿管线巡航，检测泄漏，提交检修坐标",
        "target": {
          "name": "管线接头 B12",
          "points": [{
            "frame": 0,
            "x": 0.66,
            "y": 0.68
          },
          {
            "frame": 70,
            "x": 0.52,
            "y": 0.55
          },
          {
            "frame": 140,
            "x": 0.39,
            "y": 0.45
          },
          {
            "frame": 239,
            "x": 0.39,
            "y": 0.45
          }]
        },
        "stages": [{
          "mode": "search",
          "in": 0,
          "out": 90,
          "text": "巡航扫描管线"
        },
        {
          "mode": "detect",
          "in": 90,
          "out": 165,
          "text": "识别压力异常 · 复核接头"
        },
        {
          "mode": "confirm",
          "in": 165,
          "out": 240,
          "text": "异常已确认 · 生成检修工单"
        }],
        "telemetry": [{
          "label": "水深",
          "value": 36.5,
          "unit": "m"
        },
        {
          "label": "管线压力",
          "value": 2.4,
          "unit": "MPa"
        },
        {
          "label": "温度",
          "value": 11.2,
          "unit": "°C"
        },
        {
          "label": "异常概率",
          "value": 94,
          "unit": "%"
        },
        {
          "label": "距目标",
          "value": 7.8,
          "unit": "m"
        },
        {
          "label": "定位误差",
          "value": 0.3,
          "unit": "m"
        }],
        "palette": ["#061a24", "#def7ee", "#63d7bc", "#ffc778"],
        "seed": 2147483647
      }
    }],
    recipe:{
      "适用/不适用": "适用不确定性逐步收敛的追踪或监测；不适用把随机数当真实测量。",
      "视觉隐喻": "搜索区域收束到一个可行动的目标。",
      "4–6拍": ["建立扫描区域", "跟随目标", "发现信号", "缩小锁定框", "遥测收敛", "确认并停留"],
      "不可删特征": "目标与括号同一锚点、连续轨迹、搜索→发现→确认、终值真实收敛。",
      "可替语义": "目标轨迹、状态字、遥测标签数值单位与时间窗。编辑练习：插入一个轨迹点，将一项终值改为0.125，确认停留时完整保留该数值。",
      "动作/转场/声音点": "扫描与发现可作声音点；确认后停止数字扰动。本页无音轨。",
      "失败反例": "括号跳到屏幕中心、最终仍滚假数、最大框压住目标名称。"
    },
    validate,
    render
  };
})();
