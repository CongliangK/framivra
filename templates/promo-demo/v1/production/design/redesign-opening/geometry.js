/* Original design geometry; MIT, 2026 Framivra contributors. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.OpeningDesignModel=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){'use strict';const data={
  "format": "framivra-opening-design-model/1",
  "status": "ISOLATED_DESIGN_CANDIDATE_NOT_DEFAULT_FILM",
  "fps": 30,
  "frames": 300,
  "size": [
    1920,
    1080
  ],
  "viewBox": [
    0,
    0,
    1280,
    720
  ],
  "bird": {
    "body": [
      [
        -160,
        38
      ],
      [
        -104,
        8
      ],
      [
        -146,
        -14
      ],
      [
        -65,
        -6
      ],
      [
        -15,
        -25
      ],
      [
        42,
        -18
      ],
      [
        77,
        -8
      ],
      [
        89,
        12
      ],
      [
        36,
        31
      ],
      [
        -43,
        29
      ]
    ],
    "tailface": [
      [
        -160,
        38
      ],
      [
        -104,
        8
      ],
      [
        -43,
        29
      ]
    ],
    "wing": [
      [
        -120,
        -137
      ],
      [
        -60,
        17
      ],
      [
        45,
        7
      ],
      [
        -6,
        -56
      ]
    ],
    "wingfold": [
      [
        -120,
        -137
      ],
      [
        -17,
        -5
      ],
      [
        -60,
        17
      ]
    ],
    "wingback": [
      [
        -48,
        -151
      ],
      [
        10,
        -31
      ],
      [
        75,
        -25
      ],
      [
        46,
        -44
      ]
    ],
    "head": [
      [
        48,
        -11
      ],
      [
        65,
        -48
      ],
      [
        91,
        -64
      ],
      [
        111,
        -55
      ],
      [
        117,
        -37
      ],
      [
        101,
        -17
      ],
      [
        78,
        10
      ],
      [
        59,
        19
      ]
    ],
    "beak": [
      [
        111,
        -51
      ],
      [
        141,
        -43
      ],
      [
        114,
        -37
      ]
    ],
    "eye": [
      103,
      -46
    ],
    "hinge": [
      -17,
      -5
    ],
    "scraps": [
      [
        [
          48,
          -11
        ],
        [
          40,
          -57
        ],
        [
          65,
          -48
        ]
      ],
      [
        [
          101,
          -17
        ],
        [
          119,
          19
        ],
        [
          78,
          10
        ]
      ]
    ],
    "blank": [
      [
        48,
        -11
      ],
      [
        40,
        -57
      ],
      [
        65,
        -48
      ],
      [
        91,
        -64
      ],
      [
        111,
        -55
      ],
      [
        117,
        -37
      ],
      [
        101,
        -17
      ],
      [
        119,
        19
      ],
      [
        78,
        10
      ],
      [
        59,
        19
      ]
    ]
  },
  "palette": {
    "paper": "#F1EDE1",
    "white": "#FAF7EE",
    "ink": "#253B40",
    "dark": "#233035",
    "red": "#D9533B",
    "rust": "#B83F31",
    "salmon": "#E77754",
    "slate": "#53747A",
    "mid": "#819899",
    "pale": "#D9DDD2",
    "stock": "#C6C8B8",
    "amber": "#D9AE53",
    "line": "#A8B5AE"
  },
  "stock": [
    -174,
    -166,
    326,
    250
  ],
  "anchors": {
    "stock": [
      625,
      435
    ],
    "rest": [
      625,
      491.8
    ],
    "scale": 1.4,
    "table_y": 559,
    "feet_y": 48
  },
  "events": {
    "upper_contact": 18,
    "upper_waste_release": 28,
    "upper_clear": 55,
    "lower_contact": 67,
    "lower_release": 80,
    "feet_land": 96,
    "trim_contact": 109,
    "trim_release": 112,
    "trim_clear": 139,
    "eye_contact": 150,
    "eye_clear": 163,
    "recognition_end": 190,
    "launch_impulse": 206,
    "flight_read": 239,
    "wing_approach": 264,
    "cover": 299
  },
  "visual_roles": [
    "T01",
    "T02",
    "T04",
    "T06",
    "T08",
    "T07",
    "T09"
  ],
  "provenance": {
    "art_direction": "D2 608c35945d869a48f1595b7159f15c5c33c77a4f30a76db5ed0c1d29d3429dd2; limited independent admission, not film approval",
    "copyright": "2026 Framivra contributors",
    "license": "MIT",
    "geometry": "Original paper-swift and single-station design. No reference artwork or code copied."
  }
};function freeze(o){Object.values(o).forEach(v=>{if(v&&typeof v==='object')freeze(v);});return Object.freeze(o);}return freeze(data);});
