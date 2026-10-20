(()=> {
  'use strict';
  const P=['#faf5e8',
  '#202730',
  '#e66b42',
  '#49a69d'];
  const recipe= {
    "适用/不适用":"任务与学习路径；不用于小字长段解释",
    "视觉隐喻":"每个障碍对应一个认知动作",
    "4–6拍":["提出任务",
    "跳过阻力",
    "低头穿越",
    "取得线索",
    "奖励与回顾"],
    "不可删特征":"整数格点、清晰中姿势、世界连续卷动、奖励可读",
    "可替语义":"任务、障碍类型、标签、动作窗、奖励",
    "动作/转场/声音点":"跳跃落地/收集/奖励是候选音点；无实际音轨",
    "失败反例":"两帧瞬移没有腾空，或背景跨格时整排重置"
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
    id:"pixel",
    title:"像素任务：动作承载意义",
    limits:"Canvas整数格点设计参考；六倍放大的原生网格，系统字体须在目标设备验证。无音轨。",
    examples:[ {
      "name":"知识碎片任务",
      "frames":300,
      "data": {
        "mission":"像素任务：找回知识碎片",
        "steps":[ {
          "label":"跳过信息噪声",
          "in":0,
          "out":60,
          "obstacle":"jump"
        },
        {
          "label":"跨越数据鸿沟",
          "in":60,
          "out":120,
          "obstacle":"gap"
        },
        {
          "label":"低头穿过偏见",
          "in":120,
          "out":180,
          "obstacle":"duck"
        },
        {
          "label":"收集最后证据",
          "in":180,
          "out":240,
          "obstacle":"collect"
        }],
        "reward": {
          "text":"任务完成！证据 +4",
          "in":240
        },
        "seed":23
      }
    },
    {
      "name":"社区修复任务",
      "frames":300,
      "data": {
        "mission":"像素任务：修复社区花园",
        "steps":[ {
          "label":"越过倒木",
          "in":0,
          "out":60,
          "obstacle":"jump"
        },
        {
          "label":"跨过沟渠",
          "in":60,
          "out":120,
          "obstacle":"gap"
        },
        {
          "label":"穿过藤架",
          "in":120,
          "out":180,
          "obstacle":"duck"
        },
        {
          "label":"收集种子",
          "in":180,
          "out":240,
          "obstacle":"collect"
        }],
        "reward": {
          "text":"花园重新开放",
          "in":240
        },
        "seed":-19
      }
    }],
    recipe,
    // Each action needs launch, middle pose and landing samples; reward follows the path.
    validate:(d,N,h)=> {
      common(d,N,h);
      h.int(d.seed??0,'seed',-2147483648,2147483647);
      if(!Array.isArray(d.steps)||!d.steps.length||d.steps.length>8)throw Error('steps');
      let end=0;
      d.steps.forEach(s=> {
        h.label(s.label,'step',35);
        h.int(s.in,'in',end,N-8);
        h.int(s.out,'out',s.in+7,N-1);
        if(!['jump','gap','duck','collect'].includes(s.obstacle))throw Error('action');
        end=s.out;
      });
      h.int(d.reward.in,'reward',end,N-1);
      h.label(d.reward.text,'reward',40);
    },
    // Derive player and world camera directly from the action window.
    render:(c,d,f,N,h)=> {
      const p=d.palette??P,
      step=d.steps.find(s=>s.in<=f&&f<s.out),
      i=step?d.steps.indexOf(step):d.steps.filter(s=>s.out<=f).length,
      u=step?(f-step.in)/(step.out-step.in-1):0,
      world=35+i*180+(step?Math.round(u*180):0),
      camera=Math.max(0,world-75),
      floor=142;
      header(c, {
        mission:d.mission,palette:p
      },h);
      c.save();
      c.scale(6,6);
      // Keep skyline identities in world space so crossing a cell never resets the row.
      const offset=Math.floor(camera/2),
      seed=d.seed??0;
      for(let k=Math.floor(offset/47)-1;
      k<Math.floor((offset+320)/47)+2;
      k++) {
        const x=k*47-offset,
        height=9+((k*11+seed)%31+31)%31;
        c.fillStyle=p[3];
        c.fillRect(x,floor-height,35,height);
      }
      c.fillStyle=p[1];
      c.fillRect(0,floor,320,4);
      d.steps.forEach((s,k)=> {
        let x=118+k*180-camera;
        c.fillStyle=p[2];
        if(s.obstacle==='gap') {
          c.fillStyle=p[0];
          c.fillRect(x-12,floor,26,8);
        }
        else if(s.obstacle==='duck')c.fillRect(x-18,floor-28,36,12);
        else if(s.obstacle==='collect') {
          c.fillRect(x-4,floor-30,8,8);
        }
        else c.fillRect(x-6,floor-16,12,16);
      });
      let duck=step&&step.obstacle==='duck'&&u>.22&&u<.82,
      jump=step&&step.obstacle!=='duck'?Math.round(Math.sin(Math.PI*u)*(step.obstacle==='gap'?35:24)):0,
      x=world-camera,
      y=floor-(duck?8:12)-jump;
      c.fillStyle=p[2];
      c.fillRect(x,y,8,duck?8:12);
      c.fillStyle=p[0];
      c.fillRect(x+5,y+2,2,2);
      c.restore();
      h.text(c,f>=d.reward.in?d.reward.text:(step?step.label:'准备下一步'),100,940,1720,44, {
        color:p[1]
      });
    }
  };
})();
