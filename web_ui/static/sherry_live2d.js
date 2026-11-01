/**
 * ============================================================================
 * Sherry Live2D / 2.5D Modular Interactive Companion Engine (V4 - Multi-Model)
 * ============================================================================
 * @description 支援多模型熱插拔、PNGTuber Remix 桌寵模型架構、真實眼球視線追焦、
 *              語音對嘴張合、獨立麻花辮/呆毛彈簧物理、動態手勢與表情道具。
 * @author Sherry Development Team
 * @version 4.0.0
 * ============================================================================
 */

(function (window, document) {
    'use strict';

    class SpringPhysics2D {
        constructor(stiffness = 0.08, damping = 0.82, mass = 1.0) {
            this.stiffness = stiffness;
            this.damping = damping;
            this.mass = mass;
            this.x = 0;
            this.y = 0;
            this.vx = 0;
            this.vy = 0;
        }

        update(targetX, targetY) {
            const fx = -this.stiffness * (this.x - targetX);
            const fy = -this.stiffness * (this.y - targetY);
            this.vx = (this.vx + fx / this.mass) * this.damping;
            this.vy = (this.vy + fy / this.mass) * this.damping;
            this.x += this.vx;
            this.y += this.vy;
            return { x: this.x, y: this.y };
        }
    }

    class SherryLive2DCompanion {
        constructor(containerId = 'sherry-live2d-container') {
            this.containerId = containerId;
            this.container = null;
            this.config = null;
            this.images = {};
            this.isLoaded = false;
            this.isInitializing = false;
            this.initializationScheduled = false;

            // Model Registry & Active Model
            this.modelsManifestPath = '/static/sherry_live2d/models/models.json?v=4.0.0';
            this.availableModels = [];
            this.currentModelId = 'remix_chibi';
            this.modelType = 'remix'; // 'remix' | 'classic'

            try {
                this.currentModelId = localStorage.getItem('sherry_companion_model_id') || 'remix_chibi';
            } catch (e) {}

            // Canvas & Context
            this.canvas = document.createElement('canvas');
            this.ctx = this.canvas.getContext('2d', { alpha: true });
            this.canvas.className = 'sherry-live2d-canvas';
            this.cachedCanvasRect = null;

            // Interaction & Targets
            this.targetLookX = 0;
            this.targetLookY = 0;
            this.currentLookX = 0;
            this.currentLookY = 0;

            // Physics Springs
            this.hairSpring = new SpringPhysics2D(0.09, 0.80);
            this.birdSpring = new SpringPhysics2D(0.14, 0.78);
            this.backHairSpring = new SpringPhysics2D(0.06, 0.84);
            this.braidLeftSpring = new SpringPhysics2D(0.08, 0.82);
            this.braidRightSpring = new SpringPhysics2D(0.08, 0.82);

            // Animation States
            this.time = 0;
            this.isBlinking = false;
            this.blinkProgress = 0;
            this.nextBlinkTime = Date.now() + 3000;
            this.birdFlutterProgress = 0;

            // Lip-sync / Speaking State
            this.isSpeaking = false;
            this.speakEndTime = 0;
            this.mouthState = 'closed';
            this.lastMouthToggle = 0;

            // Expression & Mood State Machine
            this.currentMood = 'idle'; // 'idle', 'happy', 'blush', 'detective', 'pout'
            this.moodTimer = 0;

            // Active Hand Gesture (Remix model: 'down', 'magnifier', 'thinking', 'wave', 'apple')
            this.currentGesture = 'down';
            this.gestureTimer = null;
            this.nextIdleActionTime = Date.now() + 12000;

            // Physics & Freefall Toss Simulation Module (Low Friction, High Elasticity)
            this.posX = null;
            this.posY = null;
            this.containerWidth = 220;
            this.containerHeight = 240;
            this.isThrown = false;
            this.isDirectDragging = false;
            this.vx = 0;
            this.vy = 0;
            this.gravity = 0.45;       // 輕盈重力加速度，增加空中滯空與彈跳時間
            this.airFriction = 0.996;   // 極低空氣阻力，維持長效彈跳動量
            this.wallBounce = 0.88;     // 兩側牆壁與天花板高彈性碰撞，支援螢幕間多次反彈
            this.floorBounce = 0.80;    // 地面高彈性反彈
            this.floorFriction = 0.97;  // 極低地面摩擦係數，溜冰場滑動質感
            this.groundedFrames = 0;
            this.tossHistory = [];
            this.flightRotation = 0;
            this.impactSquashX = 1.0;
            this.impactSquashY = 1.0;
            this.lastImpactTime = 0;

            // Headpat Gesture Tracker
            this.headpatStrokes = 0;
            this.lastHeadpatX = 0;
            this.headpatTimer = 0;

            // UI State & Persistence
            this.isMinimized = false;
            this.currentScaleIndex = 0;
            this.scalePresets = [1.0, 1.2, 0.85];

            try {
                this.isMinimized = localStorage.getItem('sherry_companion_minimized') === 'true';
                this.currentScaleIndex = parseInt(localStorage.getItem('sherry_scale_index') || '0', 10);
            } catch (e) {}

            // Frame Rate Control (~35 FPS)
            this.fpsInterval = 1000 / 35;
            this.lastDrawTime = performance.now();
            this.animationFrameId = null;
            this.renderLoop = this.renderLoop.bind(this);

            // Tab / View Tracking
            this.currentView = 'dashboard';
            this.lastViewChangeTime = 0;
            this.lastSpokenView = null;

            // Idle Chatter Interval
            this.nextIdleChatterTime = Date.now() + 45000;

            this.initDeferred();
        }

        scheduleInitialization() {
            if (this.isInitializing || this.isLoaded || this.initializationScheduled || document.hidden) return;

            this.initializationScheduled = true;
            const run = () => {
                this.initializationScheduled = false;
                if (!document.hidden) this.init();
            };

            if (typeof window.requestIdleCallback === 'function') {
                window.requestIdleCallback(run, { timeout: 2000 });
            } else {
                setTimeout(run, 300);
            }
        }

        initDeferred() {
            if (document.readyState === 'complete') {
                this.scheduleInitialization();
            } else {
                window.addEventListener('load', () => this.scheduleInitialization(), { once: true });
            }
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden && document.readyState === 'complete' && !this.isLoaded) {
                    this.scheduleInitialization();
                }
            });
        }

        async init() {
            if (this.isInitializing || this.isLoaded) return;
            this.isInitializing = true;

            try {
                this.container = document.getElementById(this.containerId);
                if (!this.container) return;

                // 1. Load Models Registry
                await this.loadModelsRegistry();

                // 2. Load Active Model
                await this.loadModel(this.currentModelId);

                // 3. Setup UI Controls
                this.setupUI();

                // 4. Setup Events
                this.bindEvents();

                // 5. Apply Saved Position & Scale
                this.applySavedPositionAndScale();

                // 6. Start Render Loop
                this.isLoaded = true;
                this.startRenderLoop();

                // Friendly Greeting on startup
                setTimeout(() => {
                    if (window.state && window.state.currentView) {
                        this.currentView = window.state.currentView;
                    }
                    this.playTimeGreeting();
                }, 1000);

            } catch (err) {
                console.warn('[SherryLive2D] Initialization notice:', err);
            } finally {
                this.isInitializing = false;
            }
        }

        async loadModelsRegistry() {
            try {
                const res = await fetch(this.modelsManifestPath);
                if (res.ok) {
                    const data = await res.json();
                    this.availableModels = data.models || [];
                    if (!this.availableModels.some(m => m.id === this.currentModelId)) {
                        this.currentModelId = data.default || (this.availableModels[0] && this.availableModels[0].id) || 'remix_chibi';
                    }
                }
            } catch (e) {
                this.availableModels = [
                    { id: 'remix_chibi', name: '橘雪莉桌寵版 (Chibi Remix)', type: 'remix', config: '/static/sherry_live2d/models/remix_chibi/model.json' },
                    { id: 'detective_v7', name: '名偵探雪莉 V7 (Classic)', type: 'classic', config: '/static/sherry_live2d/config.json' }
                ];
            }
        }

        async loadModel(modelId) {
            const modelInfo = this.availableModels.find(m => m.id === modelId) || this.availableModels[0];
            if (!modelInfo) return;

            this.currentModelId = modelInfo.id;
            this.modelType = modelInfo.type || (modelInfo.id.includes('remix') ? 'remix' : 'classic');

            try {
                localStorage.setItem('sherry_companion_model_id', this.currentModelId);
            } catch (e) {}

            const configUrl = modelInfo.config || `/static/sherry_live2d/models/${modelInfo.id}/model.json?v=4.0.0`;
            const res = await fetch(configUrl);
            if (!res.ok) return;

            this.config = await res.json();

            // Set Canvas Resolution (Downsample Remix from 900x900 to 300x300 for 220px container, saving 89% fill-rate)
            if (this.modelType === 'remix') {
                const targetDim = (this.config.canvas && (this.config.canvas.display_width || this.config.canvas.target_width)) || 300;
                this.canvas.width = targetDim;
                this.canvas.height = targetDim;
            } else {
                this.canvas.width = (this.config.canvas && this.config.canvas.width) || 270;
                this.canvas.height = (this.config.canvas && this.config.canvas.height) || 512;
            }

            if (this.container) {
                if (this.modelType === 'remix') {
                    this.container.classList.add('model-remix');
                    this.container.classList.remove('model-classic');
                    this.containerWidth = 220;
                    this.containerHeight = 240;
                } else {
                    this.container.classList.add('model-classic');
                    this.container.classList.remove('model-remix');
                    this.containerWidth = 220;
                    this.containerHeight = 380;
                }
            }

            if (!this.canvas.parentElement && this.container) {
                this.container.appendChild(this.canvas);
            }

            // Preload Textures
            await this.loadModelTextures(modelInfo);
        }

        async loadModelTextures(modelInfo) {
            this.images = {};
            const basePath = `/static/sherry_live2d/models/${modelInfo.id}/textures/`;
            const version = this.config.version || '4.0.0';

            const texturePromises = [];

            if (this.modelType === 'remix') {
                // Remix texture list
                const defaultFiles = [
                    'hat_back.png', 'hat_strap.png', 'hat_ribbon.png', 'hair_back.png',
                    'braid_left.png', 'braid_right.png', 'foot_left.png', 'foot_right.png',
                    'body_base.png', 'necktie.png', 'hand_left_down.png', 'hand_right_down.png',
                    'hand_magnifier.png', 'hand_thinking.png', 'hand_wave_left.png', 'hand_wave_right.png',
                    'hand_apple.png', 'face_base.png', 'blush.png', 'eye_iris.png', 'eye_pupil.png',
                    'eye_contour.png', 'eye_highlight.png', 'eye_closed.png', 'mouth_closed.png',
                    'mouth_open.png', 'hair_front.png', 'hair_bangs.png', 'hair_ahoge.png',
                    'hairpin.png', 'hat.png', 'bird.png', 'flower_1.png', 'flower_2.png',
                    'prop_tears.png', 'prop_sweat.png', 'prop_lightbulb.png', 'prop_glasses.png'
                ];

                defaultFiles.forEach(file => {
                    const id = file.replace('.png', '');
                    const promise = new Promise(resolve => {
                        const img = new Image();
                        let settled = false;
                        const finish = (loaded) => {
                            if (settled) return;
                            settled = true;
                            if (loaded) this.images[id] = img;
                            resolve();
                        };
                        img.addEventListener('load', () => finish(true), { once: true });
                        img.addEventListener('error', () => finish(false), { once: true });
                        img.src = `${basePath}${file}?v=${version}`;
                        if (img.complete) {
                            finish(img.naturalWidth !== 0);
                        }
                    });
                    texturePromises.push(promise);
                });

            } else {
                // Classic Detective V7
                const layers = this.config.layers || [
                    { id: 'hair_back', file: 'hair_back.png' },
                    { id: 'body', file: 'body.png' },
                    { id: 'hand_glass', file: 'hand_glass.png' },
                    { id: 'head_base', file: 'head_base.png' },
                    { id: 'blush', file: 'blush.png' }
                ];

                layers.forEach(l => {
                    const promise = new Promise(resolve => {
                        const img = new Image();
                        let settled = false;
                        const finish = (loaded) => {
                            if (settled) return;
                            settled = true;
                            if (loaded) this.images[l.id] = img;
                            resolve();
                        };
                        img.addEventListener('load', () => finish(true), { once: true });
                        img.addEventListener('error', () => finish(false), { once: true });
                        img.src = `/static/sherry_live2d/${l.file}?v=${version}`;
                        if (img.complete) {
                            finish(img.naturalWidth !== 0);
                        }
                    });
                    texturePromises.push(promise);
                });
            }

            await Promise.all(texturePromises);
        }

        async cycleModel() {
            if (this.availableModels.length <= 1) return;
            const curIdx = this.availableModels.findIndex(m => m.id === this.currentModelId);
            const nextIdx = (curIdx + 1) % this.availableModels.length;
            const nextModel = this.availableModels[nextIdx];

            this.spawnParticles('✨');
            this.spawnParticles('🌸');

            await this.loadModel(nextModel.id);
            this.say(`切換造型為「${nextModel.name}」囉！喜歡現在的雪莉嗎？💖`, 3500);
        }

        setupUI() {
            if (!this.container) return;

            // Dialogue Bubble
            this.bubbleEl = document.createElement('div');
            this.bubbleEl.className = 'sherry-speech-bubble hidden';
            this.bubbleEl.innerHTML = `
                <div class="bubble-content" id="sherry-bubble-text"></div>
                <div class="bubble-arrow"></div>
            `;
            this.container.appendChild(this.bubbleEl);

            // Action Toolbar
            this.toolbarEl = document.createElement('div');
            this.toolbarEl.className = 'sherry-companion-toolbar';
            this.toolbarEl.innerHTML = `
                <button type="button" class="sherry-tool-btn" id="sherry-btn-drag" title="按住拖曳移動" aria-label="拖曳">
                    <span class="icon">✥</span>
                </button>
                <button type="button" class="sherry-tool-btn" id="sherry-btn-skin" title="切換造型 (換裝)" aria-label="換裝">
                    <span class="icon">👘</span>
                </button>
                <button type="button" class="sherry-tool-btn" id="sherry-btn-scale" title="切換大小" aria-label="縮放">
                    <span class="icon">🔍</span>
                </button>
                <button type="button" class="sherry-tool-btn" id="sherry-btn-min" title="收合/展開" aria-label="收合">
                    <span class="icon">˗ˋˏᰔˎˊ˗</span>
                </button>
            `;
            this.container.appendChild(this.toolbarEl);

            if (this.isMinimized) {
                this.container.classList.add('minimized');
            }

            // Direct Container Click Handler for Expanding when Minimized
            this.container.addEventListener('click', (e) => {
                if (this.isMinimized) {
                    e.preventDefault();
                    e.stopPropagation();
                    this.toggleMinimize();
                }
            });

            // Bind Toolbar Controls
            const skinBtn = this.toolbarEl.querySelector('#sherry-btn-skin');
            if (skinBtn) {
                skinBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.cycleModel();
                });
            }

            const minBtn = this.toolbarEl.querySelector('#sherry-btn-min');
            if (minBtn) {
                minBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.toggleMinimize();
                });
            }

            const scaleBtn = this.toolbarEl.querySelector('#sherry-btn-scale');
            if (scaleBtn) {
                scaleBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    this.cycleScale();
                });
            }

            const dragBtn = this.toolbarEl.querySelector('#sherry-btn-drag');
            if (dragBtn) {
                this.bindDrag(dragBtn);
            }
        }

        getMovementBounds() {
            const isRemix = this.modelType === 'remix';
            const w = this.containerWidth || 220;
            const h = this.containerHeight || (isRemix ? 240 : 380);

            // 視覺邊界容許補償 (補償 Canvas 內部的透明留白邊距，使角色實體能真正貼齊螢幕邊緣)
            // Remix 模型本體在 900x900 置中，四周有留白，加上補償讓兩側與上下可移動空間最大化
            const marginX = isRemix ? 36 : 24;
            const marginTop = isRemix ? 25 : 15;
            const marginBottom = isRemix ? 25 : 15;

            const minX = -marginX;
            const maxX = Math.max(minX, window.innerWidth - w + marginX);
            const minY = -marginTop;
            const maxY = Math.max(minY, window.innerHeight - h + marginBottom);

            return { minX, maxX, minY, maxY, w, h };
        }

        bindDrag(dragHandle) {
            // Keep drag button as backup handle that triggers direct grabbing
            dragHandle.addEventListener('mousedown', (e) => {
                if (this.isMinimized) return;
                this.isThrown = false;
                this.vx = 0;
                this.vy = 0;
                this.flightRotation = 0;
                this.impactSquashX = 1.0;
                this.impactSquashY = 1.0;
                this.applyTransform();
                this.container.classList.add('is-dragging');

                const rect = this.container.getBoundingClientRect();
                this.containerWidth = this.container.offsetWidth || rect.width || 220;
                this.containerHeight = this.container.offsetHeight || rect.height || (this.modelType === 'remix' ? 240 : 380);
                this.posX = rect.left;
                this.posY = rect.top;
                this.setContainerPos(this.posX, this.posY);

                const grabX = e.clientX - this.posX;
                const grabY = e.clientY - this.posY;

                const onMove = (ev) => {
                    let newL = ev.clientX - grabX;
                    let newT = ev.clientY - grabY;
                    const { minX, maxX, minY, maxY } = this.getMovementBounds();
                    newL = Math.max(minX, Math.min(maxX, newL));
                    newT = Math.max(minY, Math.min(maxY, newT));
                    this.setContainerPos(newL, newT);
                };

                const onUp = () => {
                    document.removeEventListener('mousemove', onMove);
                    document.removeEventListener('mouseup', onUp);
                    this.container.classList.remove('is-dragging');
                    this.saveCurrentPosition();
                };

                document.addEventListener('mousemove', onMove);
                document.addEventListener('mouseup', onUp);
                e.preventDefault();
                e.stopPropagation();
            });
        }

        updateCachedGeometry() {
            if (!this.canvas) return;
            const rect = this.canvas.getBoundingClientRect();
            this.cachedCanvasRect = {
                left: rect.left,
                top: rect.top,
                width: rect.width || this.canvas.offsetWidth || 220,
                height: rect.height || this.canvas.offsetHeight || 380,
                centerX: rect.left + (rect.width || 220) / 2,
                centerY: rect.top + (rect.height || 380) * 0.45
            };
            if (this.container) {
                const cRect = this.container.getBoundingClientRect();
                this.containerWidth = this.container.offsetWidth || cRect.width || 220;
                this.containerHeight = this.container.offsetHeight || cRect.height || (this.modelType === 'remix' ? 240 : 380);
                this.containerLeft = cRect.left;
                this.containerTop = cRect.top;
            }
        }

        setContainerPos(left, top) {
            this.posX = left;
            this.posY = top;
            this.containerLeft = left;
            this.containerTop = top;
            this.container.style.left = `${left}px`;
            this.container.style.top = `${top}px`;
            this.container.style.right = 'auto';
            this.container.style.bottom = 'auto';

            if (this.cachedCanvasRect) {
                this.cachedCanvasRect.left = left;
                this.cachedCanvasRect.top = top;
                this.cachedCanvasRect.centerX = left + (this.cachedCanvasRect.width || 220) / 2;
                this.cachedCanvasRect.centerY = top + (this.cachedCanvasRect.height || 380) * 0.45;
            }
        }

        saveCurrentPosition() {
            this.updateCachedGeometry();
            try {
                if (this.posX !== null && this.posY !== null) {
                    localStorage.setItem('sherry_companion_pos_left', `${this.posX}px`);
                    localStorage.setItem('sherry_companion_pos_top', `${this.posY}px`);
                } else {
                    const rect = this.container.getBoundingClientRect();
                    localStorage.setItem('sherry_companion_pos_left', `${rect.left}px`);
                    localStorage.setItem('sherry_companion_pos_top', `${rect.top}px`);
                }
                localStorage.removeItem('sherry_companion_pos_right');
                localStorage.removeItem('sherry_companion_pos_bottom');
            } catch (e) {}
        }

        applySavedPositionAndScale() {
            try {
                const savedLeft = localStorage.getItem('sherry_companion_pos_left');
                const savedTop = localStorage.getItem('sherry_companion_pos_top');
                if (savedLeft && savedTop) {
                    this.setContainerPos(parseFloat(savedLeft), parseFloat(savedTop));
                } else {
                    const savedRight = localStorage.getItem('sherry_companion_pos_right');
                    const savedBottom = localStorage.getItem('sherry_companion_pos_bottom');
                    if (savedRight && savedBottom) {
                        this.container.style.right = savedRight;
                        this.container.style.bottom = savedBottom;
                    }
                }
                this.applyTransform();
                requestAnimationFrame(() => {
                    if (this.container) {
                        const rect = this.container.getBoundingClientRect();
                        this.containerWidth = this.container.offsetWidth || rect.width || 220;
                        this.containerHeight = this.container.offsetHeight || rect.height || 380;
                        if (this.posX === null || this.posY === null) {
                            this.posX = rect.left;
                            this.posY = rect.top;
                        }
                    }
                    this.updateCachedGeometry();
                });
            } catch (e) {}
        }

        applyTransform() {
            const scale = this.scalePresets[this.currentScaleIndex] || 1.0;
            this.container.style.transform = `scale(${scale}) rotate(${this.flightRotation}rad) scale(${this.impactSquashX}, ${this.impactSquashY})`;
        }

        cycleScale() {
            this.currentScaleIndex = (this.currentScaleIndex + 1) % this.scalePresets.length;
            this.applyTransform();
            try {
                localStorage.setItem('sherry_scale_index', this.currentScaleIndex.toString());
            } catch (e) {}
            this.updateCachedGeometry();
            const scale = this.scalePresets[this.currentScaleIndex];
            this.say(`看板娘大小已調整為 ${(scale * 100).toFixed(0)}% 囉！`, 2000);
        }

        resetToHomePosition() {
            this.isThrown = false;
            this.container.classList.remove('is-tossing');
            this.container.classList.remove('is-dragging');
            this.vx = 0;
            this.vy = 0;
            this.flightRotation = 0;
            this.impactSquashX = 1.0;
            this.impactSquashY = 1.0;
            this.container.style.left = 'auto';
            this.container.style.top = 'auto';
            this.container.style.right = '24px';
            this.container.style.bottom = '20px';
            this.applyTransform();
            try {
                localStorage.removeItem('sherry_companion_pos_left');
                localStorage.removeItem('sherry_companion_pos_top');
                localStorage.setItem('sherry_companion_pos_right', '24px');
                localStorage.setItem('sherry_companion_pos_bottom', '20px');
            } catch (e) {}
            requestAnimationFrame(() => {
                if (this.container) {
                    const rect = this.container.getBoundingClientRect();
                    this.containerWidth = this.container.offsetWidth || rect.width || 220;
                    this.containerHeight = this.container.offsetHeight || rect.height || 380;
                    this.posX = rect.left;
                    this.posY = rect.top;
                }
                this.updateCachedGeometry();
            });
            this.spawnParticles('✨');
            this.spawnParticles('🌸');
            this.say("雪莉回到主控台專屬位置囉！💖", 2500);
        }

        triggerTossDialogue() {
            this.birdFlutterProgress = 1.0;
            this.setMood('happy', 3500);
            this.spawnParticles('💨');
            this.spawnParticles('⭐');

            const tossPool = [
                "哇啊啊啊～被扔出去了！( ºΔº )💨",
                "名偵探起飛囉——！🌟",
                "快停下～頭要暈了啦！( > < )",
                "指揮官不可以亂丟雪莉啦～！(ﾉД`)੭ु⁾⁾",
                "呀～！重力加速度超載中！💫"
            ];
            this.say(tossPool[Math.floor(Math.random() * tossPool.length)], 2500);
        }

        triggerImpact(type) {
            const now = performance.now();
            if (now - this.lastImpactTime < 140) return;
            this.lastImpactTime = now;

            const speed = Math.hypot(this.vx, this.vy);
            if (speed > 3.0) {
                this.spawnParticles('💨');
                if (speed > 8.0) this.spawnParticles('⭐');

                if (type === 'floor' || type === 'roof') {
                    this.impactSquashX = 1.25;
                    this.impactSquashY = 0.75;
                } else {
                    this.impactSquashX = 0.78;
                    this.impactSquashY = 1.22;
                }

                if (speed > 7.0 && Math.random() < 0.45) {
                    const hitPool = [
                        "好痛痛…撞到牆了！( > < )",
                        "咚～！好痛呀！💥",
                        "唔唔…眼冒金星了～💫"
                    ];
                    this.say(hitPool[Math.floor(Math.random() * hitPool.length)], 1800);
                }
            }
        }

        onLanded() {
            this.container.classList.remove('is-tossing');
            this.saveCurrentPosition();
            this.spawnParticles('🌸');
            const landPool = [
                "呼…安全著陸！指揮官壞心眼！(｡・//ε//・｡)",
                "落地得分！但是不可以再丟我了喔～！(｀・ω・´)💢",
                "嗚嗚…頭暈暈的～轉圈圈～💫",
                "名偵探平安迫降！✨"
            ];
            this.say(landPool[Math.floor(Math.random() * landPool.length)], 3000);
        }

        updatePhysics() {
            if (!this.isThrown || this.isMinimized) {
                this.impactSquashX += (1.0 - this.impactSquashX) * 0.15;
                this.impactSquashY += (1.0 - this.impactSquashY) * 0.15;
                this.flightRotation += (0 - this.flightRotation) * 0.15;
                if (Math.abs(this.impactSquashX - 1.0) > 0.005 || Math.abs(this.flightRotation) > 0.005) {
                    this.applyTransform();
                }
                return;
            }

            if (this.posX === null || this.posY === null) {
                const rect = this.container.getBoundingClientRect();
                this.posX = rect.left;
                this.posY = rect.top;
                this.containerWidth = this.container.offsetWidth || rect.width || 220;
                this.containerHeight = this.container.offsetHeight || rect.height || (this.modelType === 'remix' ? 240 : 380);
            }

            const { minX, maxX, minY, maxY } = this.getMovementBounds();

            this.vy += this.gravity;
            this.vx *= this.airFriction;

            this.posX += this.vx;
            this.posY += this.vy;

            const targetRot = Math.max(-0.45, Math.min(0.45, this.vx * 0.035));
            this.flightRotation += (targetRot - this.flightRotation) * 0.25;

            if (this.modelType === 'remix') {
                this.currentGesture = 'wave';
            }

            // Left Wall
            if (this.posX <= minX) {
                this.posX = minX;
                this.vx = Math.abs(this.vx) * this.wallBounce;
                this.triggerImpact('wall');
            }
            // Right Wall
            else if (this.posX >= maxX) {
                this.posX = maxX;
                this.vx = -Math.abs(this.vx) * this.wallBounce;
                this.triggerImpact('wall');
            }

            // Roof
            if (this.posY <= minY) {
                this.posY = minY;
                this.vy = Math.abs(this.vy) * this.wallBounce;
                this.triggerImpact('roof');
            }

            // Floor
            if (this.posY >= maxY) {
                this.posY = maxY;
                this.vy = -Math.abs(this.vy) * this.floorBounce;
                this.vx *= this.floorFriction;
                this.triggerImpact('floor');

                if (Math.abs(this.vy) < 0.6 && Math.abs(this.vx) < 0.35) {
                    this.groundedFrames = (this.groundedFrames || 0) + 1;
                    if (this.groundedFrames >= 3) {
                        this.isThrown = false;
                        this.container.classList.remove('is-tossing');
                        this.vx = 0;
                        this.vy = 0;
                        this.flightRotation = 0;
                        this.groundedFrames = 0;
                        if (this.modelType === 'remix') this.currentGesture = 'down';
                        this.onLanded();
                    }
                } else {
                    this.groundedFrames = 0;
                }
            } else {
                this.groundedFrames = 0;
            }

            this.setContainerPos(this.posX, this.posY);

            this.impactSquashX += (1.0 - this.impactSquashX) * 0.15;
            this.impactSquashY += (1.0 - this.impactSquashY) * 0.15;
            this.applyTransform();
        }

        toggleMinimize() {
            this.isMinimized = !this.isMinimized;
            this.container.classList.toggle('minimized', this.isMinimized);

            try {
                localStorage.setItem('sherry_companion_minimized', this.isMinimized.toString());
            } catch (e) {}

            if (this.isMinimized) {
                this.stopRenderLoop();
            } else {
                this.startRenderLoop();
                this.say("雪莉隨時為您待命！✨", 2500);
            }
        }

        bindEvents() {
            let isPointerDown = false;
            let mouseDownX = 0, mouseDownY = 0;
            let mouseDownClientX = 0, mouseDownClientY = 0;
            let mouseDownTime = 0;
            let hasDragged = false;
            let grabOffsetX = 0, grabOffsetY = 0;

            // Listen for window resize to refresh cached coordinates
            window.addEventListener('resize', () => {
                this.updateCachedGeometry();
            });

            // Global mouse move for look tracking & direct dragging
            window.addEventListener('mousemove', (e) => {
                if (this.isMinimized) return;

                if (isPointerDown) {
                    const dist = Math.hypot(e.clientX - mouseDownClientX, e.clientY - mouseDownClientY);
                    if (!hasDragged && dist > 6) {
                        hasDragged = true;
                        this.isDirectDragging = true;
                        this.isThrown = false;
                        this.vx = 0;
                        this.vy = 0;
                        this.canvas.style.cursor = 'grabbing';
                        this.container.classList.add('is-dragging');

                        // Switch from bottom/right layout to absolute left/top layout ONLY when user actually drags
                        const startLeft = (this.containerLeft !== undefined && this.containerLeft !== null)
                            ? this.containerLeft
                            : (this.posX ?? (this.cachedCanvasRect ? this.cachedCanvasRect.left : 0));
                        const startTop = (this.containerTop !== undefined && this.containerTop !== null)
                            ? this.containerTop
                            : (this.posY ?? (this.cachedCanvasRect ? this.cachedCanvasRect.top : 0));
                        this.posX = startLeft;
                        this.posY = startTop;
                        this.setContainerPos(this.posX, this.posY);
                        grabOffsetX = e.clientX - this.posX;
                        grabOffsetY = e.clientY - this.posY;

                        if (this.modelType === 'remix') {
                            this.currentGesture = 'wave';
                        }
                        this.setMood('happy', 2500);
                        this.spawnParticles('✨');

                        const grabPool = [
                            "欸？！被抓起來了！( > < )",
                            "哇！要被帶去哪裡呀？✨",
                            "抓…抓我做什麼啦～(｡・//ε//・｡)"
                        ];
                        this.say(grabPool[Math.floor(Math.random() * grabPool.length)], 2000);
                    }

                    if (hasDragged) {
                        let newLeft = e.clientX - grabOffsetX;
                        let newTop = e.clientY - grabOffsetY;
                        const { minX, maxX, minY, maxY } = this.getMovementBounds();
                        newLeft = Math.max(minX, Math.min(maxX, newLeft));
                        newTop = Math.max(minY, Math.min(maxY, newTop));
                        this.setContainerPos(newLeft, newTop);

                        const now = performance.now();
                        this.tossHistory.push({ x: e.clientX, y: e.clientY, time: now });
                        while (this.tossHistory.length > 8 || (this.tossHistory.length > 1 && (now - this.tossHistory[0].time) > 220)) {
                            this.tossHistory.shift();
                        }
                        return;
                    }
                }

                // Normal look tracking when not dragging (Zero getBoundingClientRect in mousemove)
                if (!this.cachedCanvasRect) {
                    this.updateCachedGeometry();
                }
                const centerX = this.cachedCanvasRect ? this.cachedCanvasRect.centerX : (window.innerWidth - 110);
                const centerY = this.cachedCanvasRect ? this.cachedCanvasRect.centerY : (window.innerHeight - 200);

                const dx = (e.clientX - centerX) / (window.innerWidth / 2);
                const dy = (e.clientY - centerY) / (window.innerHeight / 2);

                this.targetLookX = Math.max(-1.0, Math.min(1.0, dx));
                this.targetLookY = Math.max(-1.0, Math.min(1.0, dy));
            });

            // Canvas Mouse Down: Start click / drag tracking
            this.canvas.addEventListener('mousedown', (e) => {
                if (this.isMinimized || e.button !== 0) return;
                isPointerDown = true;
                hasDragged = false;
                this.isDirectDragging = false;
                this.isThrown = false;
                this.container.classList.remove('is-tossing');
                this.vx = 0;
                this.vy = 0;
                this.flightRotation = 0;
                this.impactSquashX = 1.0;
                this.impactSquashY = 1.0;
                this.applyTransform();

                this.updateCachedGeometry();
                const rect = this.cachedCanvasRect || this.canvas.getBoundingClientRect();
                mouseDownX = (e.clientX - rect.left) * (this.canvas.width / rect.width);
                mouseDownY = (e.clientY - rect.top) * (this.canvas.height / rect.height);
                mouseDownClientX = e.clientX;
                mouseDownClientY = e.clientY;
                mouseDownTime = performance.now();

                this.containerWidth = this.container.offsetWidth || rect.width || 220;
                this.containerHeight = this.container.offsetHeight || rect.height || (this.modelType === 'remix' ? 240 : 380);
                grabOffsetX = e.clientX - rect.left;
                grabOffsetY = e.clientY - rect.top;

                this.tossHistory = [{ x: e.clientX, y: e.clientY, time: performance.now() }];

                this.headpatTimer = Date.now();
                this.lastHeadpatX = e.clientX;
                e.preventDefault();
            });

            // Window Mouse Up: Release or Throw!
            window.addEventListener('mouseup', (e) => {
                if (!isPointerDown) return;
                isPointerDown = false;
                this.canvas.style.cursor = '';
                this.container.classList.remove('is-dragging');

                if (hasDragged) {
                    hasDragged = false;
                    this.isDirectDragging = false;

                    const now = performance.now();
                    const lastMove = this.tossHistory[this.tossHistory.length - 1];
                    // If user paused movement before releasing (> 110ms), treat as gentle placement rather than a fling
                    const isRecent = lastMove && (now - lastMove.time) <= 110;

                    this.tossHistory.push({ x: e.clientX, y: e.clientY, time: now });
                    while (this.tossHistory.length > 8 || (this.tossHistory.length > 1 && (now - this.tossHistory[0].time) > 220)) {
                        this.tossHistory.shift();
                    }

                    const recent = this.tossHistory.filter(p => (now - p.time) <= 220);
                    let thrown = false;

                    if (isRecent && recent.length >= 2) {
                        const p1 = recent[0];
                        const p2 = recent[recent.length - 1];
                        const dt = Math.max(16, p2.time - p1.time) / 1000;
                        const targetFps = (1000 / this.fpsInterval) || 35;
                        let vx = (((p2.x - p1.x) / dt) / targetFps) * 1.2;
                        let vy = (((p2.y - p1.y) / dt) / targetFps) * 1.2;

                        const speed = Math.hypot(vx, vy);
                        const maxSpeed = 42;
                        if (speed > maxSpeed) {
                            vx = (vx / speed) * maxSpeed;
                            vy = (vy / speed) * maxSpeed;
                        }

                        if (speed > 2.0) {
                            // TOSS SHERRY!
                            this.isThrown = true;
                            this.container.classList.add('is-tossing');
                            this.vx = vx;
                            this.vy = vy;
                            this.groundedFrames = 0;
                            this.triggerTossDialogue();
                            thrown = true;
                        }
                    }

                    if (!thrown) {
                        // Gentle drop
                        this.isThrown = false;
                        this.vx = 0;
                        this.vy = 0;
                        this.flightRotation = 0;
                        this.container.classList.remove('is-tossing');
                        this.saveCurrentPosition();
                        if (this.modelType === 'remix') this.currentGesture = 'down';
                        this.say("呼…安全降落！🌸", 2000);
                    }
                } else {
                    // Normal click interaction
                    this.handleHitboxClick(mouseDownX, mouseDownY);
                }
            });

            // Double Click to Reset Home Position
            this.canvas.addEventListener('dblclick', (e) => {
                e.preventDefault();
                this.resetToHomePosition();
            });

            document.addEventListener('visibilitychange', () => {
                if (document.hidden) {
                    this.stopRenderLoop();
                } else if (this.isLoaded && !this.isMinimized) {
                    this.startRenderLoop();
                }
            });
        }

        onViewChanged(viewId) {
            if (!viewId || this.isMinimized) return;
            this.currentView = viewId;

            const now = Date.now();
            if (now - this.lastViewChangeTime < 1200 && this.lastSpokenView === viewId) {
                return;
            }
            this.lastViewChangeTime = now;
            this.lastSpokenView = viewId;

            // Automatically switch hands/gestures based on tab!
            if (viewId === 'logs' || viewId === 'audit') {
                this.currentGesture = 'magnifier';
            } else if (viewId === 'chat') {
                this.currentGesture = 'thinking';
            } else {
                this.currentGesture = 'down';
            }

            let pool = null;
            if (this.config && this.config.dialogues && this.config.dialogues.views_welcome) {
                pool = this.config.dialogues.views_welcome[viewId] || this.config.dialogues.views_welcome['default'];
            }
            if (!pool || pool.length === 0) {
                pool = ["歡迎來到 " + viewId + "！雪莉隨時為您提供協助～✨"];
            }

            const text = pool[Math.floor(Math.random() * pool.length)];
            this.triggerViewAtmosphere(viewId);
            this.say(text, 4000);
        }

        triggerViewAtmosphere(viewId) {
            switch (viewId) {
                case 'logs':
                case 'audit':
                    this.setMood('detective', 3500);
                    this.spawnParticles('🔍');
                    break;
                case 'chat':
                    this.setMood('happy', 3500);
                    this.spawnParticles('💬');
                    this.spawnParticles('🌸');
                    break;
                case 'settings':
                    this.setMood('happy', 3500);
                    this.spawnParticles('⚙️');
                    break;
                default:
                    this.setMood('happy', 3000);
                    this.spawnParticles('✨');
                    break;
            }
        }

        setGesture(gesture, durationMs = 3500) {
            this.currentGesture = gesture;
            clearTimeout(this.gestureTimer);
            if (gesture !== 'down' && durationMs > 0) {
                this.gestureTimer = setTimeout(() => {
                    if (this.currentGesture === gesture && !this.isThrown && !this.isDirectDragging) {
                        this.currentGesture = 'down';
                    }
                }, durationMs);
            }
        }

        triggerRandomIdleAction() {
            if (this.isMinimized || this.modelType !== 'remix' || this.isSpeaking || this.isThrown || this.isDirectDragging) return;
            const actions = [
                () => {
                    this.setGesture('apple', 4500);
                    this.setMood('happy', 3500);
                    this.spawnParticles('🍎');
                    this.say("指揮官工作辛苦了，雪莉分你一顆甜蘋果！🍎✨", 3500);
                },
                () => {
                    this.setGesture('magnifier', 4200);
                    this.setMood('detective', 4000);
                    this.spawnParticles('🔍');
                    this.say("（仔細用放大鏡檢查控制台狀態…）目前一切正常！✨", 3500);
                },
                () => {
                    this.setGesture('wave', 3500);
                    this.setMood('happy', 3000);
                    this.spawnParticles('✨');
                    this.say("哈囉指揮官～雪莉一直都在這裡陪著你喔！🎵", 3000);
                },
                () => {
                    this.setGesture('thinking', 4000);
                    this.setMood('detective', 3500);
                    this.say("唔…今天有沒有什麼有趣的謎題需要名偵探雪莉破解呢？🤔", 3500);
                },
                () => {
                    this.setMood('detective', 4000);
                    this.setGesture('magnifier', 4000);
                    this.spawnParticles('💡');
                    this.say("靈光一閃！名偵探雪莉洞悉了一切！🎩💡", 3500);
                }
            ];
            const pick = actions[Math.floor(Math.random() * actions.length)];
            pick();
        }

        triggerHeadpat(isCombo = false) {
            this.setMood('blush', 4000);
            this.setGesture('wave', 3500);
            this.spawnParticles('💕');
            this.spawnParticles('🌸');
            this.birdFlutterProgress = 1.0;

            if (Math.random() < 0.3) {
                this.setMood('detective', 3500);
                this.setGesture('magnifier', 3500);
                this.spawnParticles('💡');
            }

            let pool = (this.config && this.config.dialogues && this.config.dialogues[isCombo ? 'headpat_combo' : 'touch_head']) || [
                "哇…被摸頭好舒服喔~ 嘿嘿 (´,,•ω•,,)",
                "最喜歡被指揮官溫柔摸頭了～心情值 MAX！🌸✨",
                "（開心眨眼）摸頭能大幅提升名偵探的推理敏銳度唷！🎵"
            ];
            this.say(pool[Math.floor(Math.random() * pool.length)], 3500);
        }

        handleHitboxClick(x, y) {
            let hitAction = 'touch_body';

            // Scale coordinates if Remix model is downsampled from 900x900
            if (this.modelType === 'remix' && this.canvas.width && this.canvas.width !== 900) {
                const normScale = 900 / this.canvas.width;
                x = x * normScale;
                y = y * normScale;
            }

            if (this.config && this.config.hitboxes) {
                for (let hb of this.config.hitboxes) {
                    const dist = Math.hypot(x - hb.x, y - hb.y);
                    if (dist <= hb.radius) {
                        hitAction = hb.action || hb.name;
                        break;
                    }
                }
            } else {
                // High-precision Hitbox Zones for 900x900 Canvas
                if (y < 230 && Math.abs(x - 450) < 180) {
                    hitAction = 'touch_bird';
                } else if (y < 340) {
                    hitAction = 'touch_head';
                } else if (y < 510) {
                    hitAction = 'poke_cheek';
                } else if (y < 690) {
                    hitAction = 'touch_chest';
                } else {
                    hitAction = 'touch_skirt';
                }
            }

            switch (hitAction) {
                case 'touch_bird':
                    this.birdFlutterProgress = 1.0;
                    this.setMood('happy', 3500);
                    this.setGesture('wave', 3500);
                    this.spawnParticles('🕊️');
                    this.spawnParticles('✨');
                    const birdPool = (this.config && this.config.dialogues && this.config.dialogues.touch_bird) || [
                        "啾啾！小鳥說今天也是充滿希望的一天！🕊️",
                        "哇，這頂帽子上的小鳥是我的特別偵探助手唷！🎵"
                    ];
                    this.say(birdPool[Math.floor(Math.random() * birdPool.length)], 3500);
                    break;

                case 'touch_head':
                case 'hat':
                    this.triggerHeadpat(false);
                    break;

                case 'poke_cheek': {
                    const r = Math.random();
                    if (r < 0.45) {
                        this.setMood('blush', 3500);
                        this.spawnParticles('💕');
                    } else if (r < 0.75) {
                        this.setMood('sweat', 3500);
                        this.spawnParticles('💧');
                    } else {
                        this.setMood('pout', 3500);
                        this.spawnParticles('💦');
                    }
                    this.setGesture('thinking', 3500);
                    const poutPool = (this.config && this.config.dialogues && this.config.dialogues.poke_cheek) || [
                        "不要一直捏我的臉啦~ 會變圓的！(｡・//ε//・｡)💢",
                        "哇哇！臉頰很軟對吧？但戳太久會害羞的…⁄(⁄ ⁄•⁄-⁄•⁄ ⁄)⁄",
                        "唔唔…捏臉頰會讓推理思路短路啦！( > < )"
                    ];
                    this.say(poutPool[Math.floor(Math.random() * poutPool.length)], 3500);
                    break;
                }

                case 'touch_glass':
                    this.setMood('detective', 4000);
                    this.setGesture('magnifier', 4000);
                    this.spawnParticles('🔍');
                    const glassPool = (this.config && this.config.dialogues && this.config.dialogues.touch_glass) || [
                        "真相永遠只有一個！讓我和你一起找出答案吧！✨",
                        "透過這個放大鏡，任何異常都逃不過我的眼睛！🔎"
                    ];
                    this.say(glassPool[Math.floor(Math.random() * glassPool.length)], 4000);
                    break;

                case 'touch_chest':
                case 'touch_body':
                default: {
                    const roll = Math.random();
                    if (roll < 0.35) {
                        this.setGesture('apple', 4000);
                        this.setMood('happy', 4000);
                        this.spawnParticles('🍎');
                        this.say("指揮官肚子餓了嗎？雪莉剛洗好的紅蘋果分你一顆！🍎✨", 3500);
                    } else if (roll < 0.70) {
                        this.setGesture('magnifier', 4000);
                        this.setMood('detective', 4000);
                        this.spawnParticles('🔍');
                        this.say("（端正領結拿出放大鏡）名偵探雪莉，隨時準備好為指揮官出動！🎩🔎", 3500);
                    } else {
                        this.setGesture('wave', 3500);
                        this.setMood('happy', 3500);
                        this.spawnParticles('✨');
                        this.say("我是雪莉，今天也在隨時為指揮官待命喔！有什麼想聊聊的嗎？💬💖", 3500);
                    }
                    break;
                }

                case 'touch_skirt':
                    this.setMood('sweat', 3500);
                    this.setGesture('wave', 3000);
                    this.spawnParticles('💨');
                    this.spawnParticles('💢');
                    const skirtPool = (this.config && this.config.dialogues && this.config.dialogues.touch_skirt) || [
                        "呀！不可以亂點那裡啦！會被當成可疑人物的！( > < )💢",
                        "指揮官！偵探手冊明文規定不准惡作劇喔！(｡・//ε//・｡)"
                    ];
                    this.say(skirtPool[Math.floor(Math.random() * skirtPool.length)], 3500);
                    break;
            }
        }

        playTimeGreeting() {
            const hour = new Date().getHours();
            let key = "greeting_night";
            if (hour >= 5 && hour < 11) key = "greeting_morning";
            else if (hour >= 11 && hour < 17) key = "greeting_afternoon";
            else if (hour >= 17 && hour < 22) key = "greeting_evening";

            let pool = (this.config && this.config.dialogues && this.config.dialogues[key]) || [
                "您好！名偵探雪莉隨時在您身邊為您守護！✨"
            ];
            this.setMood('happy', 3500);
            this.spawnParticles('✨');
            this.say(pool[Math.floor(Math.random() * pool.length)], 4500);
        }

        triggerIdleChatter() {
            if (this.isSpeaking || this.isMinimized) return;
            const idlePool = (this.config && this.config.dialogues && this.config.dialogues.idle_chatter) || [
                "（正在認真翻閱偵探筆記…）📚",
                "今天伺服器的運作非常穩定順暢呢~ ✨",
                "好想吃剛出爐的藍莓鬆餅喔…🍰",
                "指揮官，有什麼需要雪莉為您調取的資料嗎？💖"
            ];
            this.say(idlePool[Math.floor(Math.random() * idlePool.length)], 4000);
        }

        setMood(mood, duration = 3000) {
            this.currentMood = mood;
            this.moodTimer = Date.now() + duration;
        }

        say(text, durationMs = 3500) {
            if (this.isMinimized || !this.container) return;

            try {
                const textEl = this.container.querySelector('#sherry-bubble-text');
                if (textEl && this.bubbleEl) {
                    textEl.innerText = text;
                    this.bubbleEl.classList.remove('hidden');

                    clearTimeout(this.bubbleTimer);
                    this.bubbleTimer = setTimeout(() => {
                        this.hideBubble();
                    }, durationMs);
                }

                this.isSpeaking = true;
                this.speakEndTime = Date.now() + durationMs;
            } catch (e) {}
        }

        hideBubble() {
            if (this.bubbleEl) {
                this.bubbleEl.classList.add('hidden');
            }
        }

        spawnParticles(char = '✨') {
            if (!this.container) return;
            try {
                const particle = document.createElement('div');
                particle.className = 'sherry-particle';
                particle.innerText = char;
                particle.style.left = `${Math.random() * 60 + 20}%`;
                particle.style.top = `${Math.random() * 40 + 10}%`;
                this.container.appendChild(particle);

                setTimeout(() => {
                    particle.remove();
                }, 1200);
            } catch (e) {}
        }

        startRenderLoop() {
            if (this.animationFrameId !== null || document.hidden || this.isMinimized || !this.isLoaded) return;
            this.lastDrawTime = performance.now();
            this.animationFrameId = requestAnimationFrame(this.renderLoop);
        }

        stopRenderLoop() {
            if (this.animationFrameId === null) return;
            cancelAnimationFrame(this.animationFrameId);
            this.animationFrameId = null;
        }

        renderLoop() {
            this.animationFrameId = null;
            if (document.hidden || this.isMinimized || !this.isLoaded) return;

            this.animationFrameId = requestAnimationFrame(this.renderLoop);
            const now = performance.now();
            const elapsed = now - this.lastDrawTime;

            if (elapsed < this.fpsInterval) return;
            this.lastDrawTime = now - (elapsed % this.fpsInterval);

            // Freefall / Toss Physics Simulation & Squish recovery
            this.updatePhysics();

            if (Date.now() > this.nextIdleChatterTime) {
                this.triggerIdleChatter();
                this.nextIdleChatterTime = Date.now() + 60000 + Math.random() * 60000;
            }

            if (Date.now() > this.nextIdleActionTime) {
                this.triggerRandomIdleAction();
                this.nextIdleActionTime = Date.now() + 14000 + Math.random() * 16000;
            }

            if (this.modelType === 'remix') {
                this.renderRemixModel();
            } else {
                this.renderClassicModel();
            }
        }

        /**
         * 渲染全新高精細 2.5D PNGTuber Remix 桌寵雪莉
         */
        renderRemixModel() {
            this.time += 0.04;
            const now = Date.now();

            const scale = this.canvas.width / 900;
            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
            this.ctx.save();
            this.ctx.scale(scale, scale);

            // Interpolate Look Offset
            this.currentLookX += (this.targetLookX - this.currentLookX) * 0.08;
            this.currentLookY += (this.targetLookY - this.currentLookY) * 0.08;

            // Spring Physics
            const hairPhys = this.hairSpring.update(this.currentLookX * 6.0, this.currentLookY * 4.0);
            const birdPhys = this.birdSpring.update(this.currentLookX * 3.0, (this.birdFlutterProgress > 0 ? -16.0 : 0));
            const leftBraidPhys = this.braidLeftSpring.update(this.currentLookX * 4.0, this.currentLookY * 3.0);
            const rightBraidPhys = this.braidRightSpring.update(this.currentLookX * 4.0, this.currentLookY * 3.0);

            if (this.birdFlutterProgress > 0) this.birdFlutterProgress -= 0.05;
            if (this.currentMood !== 'idle' && now > this.moodTimer) this.currentMood = 'idle';

            // Auto-Blink Logic
            if (now > this.nextBlinkTime && !this.isBlinking) {
                this.isBlinking = true;
                this.blinkProgress = 0;
            }

            let showClosedEye = false;
            if (this.isBlinking) {
                this.blinkProgress += 0.2;
                if (this.blinkProgress >= 1.0) {
                    this.isBlinking = false;
                    this.nextBlinkTime = now + 3000 + Math.random() * 4000;
                } else if (this.blinkProgress > 0.25 && this.blinkProgress < 0.8) {
                    showClosedEye = true;
                }
            }

            // Lip-sync Speaking Logic
            if (this.isSpeaking) {
                if (now > this.speakEndTime) {
                    this.isSpeaking = false;
                    this.mouthState = 'closed';
                } else {
                    if (now - this.lastMouthToggle > 150) {
                        this.mouthState = (this.mouthState === 'open') ? 'closed' : 'open';
                        this.lastMouthToggle = now;
                    }
                }
            } else {
                this.mouthState = (this.currentMood === 'happy') ? 'open' : 'closed';
            }

            // Natural Oscillations
            const breathing = Math.sin(this.time * 2.2) * 8.0;
            const birdBounce = Math.sin(this.time * 4.0) * 3.5 + birdPhys.y;
            const shyWiggle = (this.currentMood === 'blush') ? Math.sin(this.time * 6.0) * 3.5 : 0;

            const drawLayer = (imgId) => {
                const img = this.images[imgId];
                if (img) this.ctx.drawImage(img, 0, 0);
            };

            // 1. Hat Back & Straps (z = -2)
            drawLayer('hat_back');
            drawLayer('hat_strap');
            drawLayer('hat_ribbon');

            // 2. Hair Back & Braids (z = -1)
            drawLayer('hair_back');

            // Left Braid with spring physics
            if (this.images['braid_left']) {
                this.ctx.save();
                const p = [255, 460];
                this.ctx.translate(p[0], p[1] + breathing * 0.4);
                this.ctx.rotate(Math.sin(this.time * 2.2) * 0.08 + leftBraidPhys.x * 0.03);
                this.ctx.drawImage(this.images['braid_left'], -p[0], -p[1]);
                this.ctx.restore();
            }

            // Right Braid with spring physics
            if (this.images['braid_right']) {
                this.ctx.save();
                const p = [645, 460];
                this.ctx.translate(p[0], p[1] + breathing * 0.4);
                this.ctx.rotate(-Math.sin(this.time * 2.2) * 0.08 + rightBraidPhys.x * 0.03);
                this.ctx.drawImage(this.images['braid_right'], -p[0], -p[1]);
                this.ctx.restore();
            }

            // 3. Body & Torso
            this.ctx.save();
            this.ctx.translate(0, breathing * 0.7);
            drawLayer('foot_right');
            drawLayer('foot_left');
            drawLayer('body_base');
            drawLayer('necktie');

            // 4. Dynamic Animated Hands based on Gesture / Interaction
            if (this.currentGesture === 'magnifier' && this.images['hand_magnifier']) {
                this.ctx.save();
                const rot = Math.sin(this.time * 2.5) * 0.08;
                this.ctx.translate(450, 450);
                this.ctx.rotate(rot);
                this.ctx.drawImage(this.images['hand_magnifier'], -450, -450);
                this.ctx.restore();
            } else if (this.currentGesture === 'thinking' && this.images['hand_thinking']) {
                this.ctx.save();
                const rot = Math.sin(this.time * 2.0) * 0.04;
                this.ctx.translate(450, 480);
                this.ctx.rotate(rot);
                this.ctx.drawImage(this.images['hand_thinking'], -450, -480);
                this.ctx.restore();
            } else if (this.currentGesture === 'wave') {
                if (this.images['hand_wave_left']) {
                    this.ctx.save();
                    const pivotL = [270, 520];
                    this.ctx.translate(pivotL[0], pivotL[1]);
                    this.ctx.rotate(Math.sin(this.time * 7.0) * 0.14);
                    this.ctx.drawImage(this.images['hand_wave_left'], -pivotL[0], -pivotL[1]);
                    this.ctx.restore();
                }
                if (this.images['hand_wave_right']) {
                    this.ctx.save();
                    const pivotR = [630, 520];
                    this.ctx.translate(pivotR[0], pivotR[1]);
                    this.ctx.rotate(-Math.sin(this.time * 7.0) * 0.14);
                    this.ctx.drawImage(this.images['hand_wave_right'], -pivotR[0], -pivotR[1]);
                    this.ctx.restore();
                }
            } else if (this.currentGesture === 'apple' && this.images['hand_apple']) {
                this.ctx.save();
                const rot = Math.sin(this.time * 2.5) * 0.05;
                this.ctx.translate(450, 500);
                this.ctx.rotate(rot);
                this.ctx.drawImage(this.images['hand_apple'], -450, -500);
                this.ctx.restore();
            } else {
                // Natural breathing swing for hands when resting down
                if (this.images['hand_left_down']) {
                    this.ctx.save();
                    const pL = [270, 490];
                    this.ctx.translate(pL[0], pL[1]);
                    this.ctx.rotate(Math.sin(this.time * 2.2) * 0.05);
                    this.ctx.drawImage(this.images['hand_left_down'], -pL[0], -pL[1]);
                    this.ctx.restore();
                }
                if (this.images['hand_right_down']) {
                    this.ctx.save();
                    const pR = [630, 490];
                    this.ctx.translate(pR[0], pR[1]);
                    this.ctx.rotate(-Math.sin(this.time * 2.2) * 0.05);
                    this.ctx.drawImage(this.images['hand_right_down'], -pR[0], -pR[1]);
                    this.ctx.restore();
                }
            }
            this.ctx.restore(); // End Body & Hands

            // 5. Head Group (Rotates and moves with Mouse Look & Headpat)
            this.ctx.save();
            const headPivot = [450, 480];
            const headOffsetX = this.currentLookX * 22.0 + shyWiggle;
            const headOffsetY = this.currentLookY * 16.0 + (this.isSpeaking ? Math.sin(this.time * 12.0) * 2.5 : 0) + breathing * 0.35;
            const headRot = this.currentLookX * 0.045;

            this.ctx.translate(headPivot[0] + headOffsetX, headPivot[1] + headOffsetY);
            this.ctx.rotate(headRot);

            const drawHeadPart = (imgId) => {
                const img = this.images[imgId];
                if (img) this.ctx.drawImage(img, -headPivot[0], -headPivot[1]);
            };

            // 5a. Face Base
            drawHeadPart('face_base');

            // 5b. Eyes System (Eye Tracking + Blinking)
            drawHeadPart('eye_contour');

            if (showClosedEye) {
                drawHeadPart('eye_closed');
            } else {
                // Real Pupil & Iris Tracking Mouse
                const eyeMaxX = 16;
                const eyeMaxY = 12;
                const eyeDx = this.currentLookX * eyeMaxX;
                const eyeDy = this.currentLookY * eyeMaxY;

                this.ctx.save();
                this.ctx.translate(eyeDx, eyeDy);
                drawHeadPart('eye_iris');
                drawHeadPart('eye_pupil');
                this.ctx.restore();

                this.ctx.save();
                this.ctx.translate(eyeDx * 0.6, eyeDy * 0.6);
                drawHeadPart('eye_highlight');
                this.ctx.restore();
            }

            // 5c. Mouth System (Speaking Lip-sync)
            if (this.mouthState === 'open') {
                drawHeadPart('mouth_open');
            } else {
                drawHeadPart('mouth_closed');
            }

            // 5d. Blush
            if (this.currentMood === 'blush' || this.currentMood === 'happy') {
                this.ctx.save();
                this.ctx.globalAlpha = 0.85 + Math.sin(this.time * 4.0) * 0.15;
                drawHeadPart('blush');
                this.ctx.restore();
            }

            // 5e. Hair Front & Ahoge (Ahoge Wiggle Spring)
            drawHeadPart('hair_front');
            drawHeadPart('hair_bangs');

            if (this.images['hair_ahoge']) {
                this.ctx.save();
                const ahogePivot = [432, 200];
                this.ctx.translate(ahogePivot[0] - headPivot[0], ahogePivot[1] - headPivot[1]);
                this.ctx.rotate(Math.sin(this.time * 3.5) * 0.14 + hairPhys.x * 0.04);
                this.ctx.drawImage(this.images['hair_ahoge'], -ahogePivot[0], -ahogePivot[1]);
                this.ctx.restore();
            }

            drawHeadPart('hairpin');

            // 5f. Hat, Bird & Flowers
            drawHeadPart('hat');

            if (this.images['bird']) {
                this.ctx.save();
                this.ctx.translate(0, birdBounce);
                this.ctx.rotate(Math.sin(this.time * 5.0) * 0.06);
                drawHeadPart('bird');
                this.ctx.restore();
            }

            drawHeadPart('flower_1');
            drawHeadPart('flower_2');

            // 5g. Dynamic Mood Props (Steady & mood-specific, no flickering)
            if (this.currentMood === 'detective') {
                drawHeadPart('prop_lightbulb');
                drawHeadPart('prop_glasses');
            } else if (this.currentMood === 'pout') {
                drawHeadPart('prop_tears');
            } else if (this.currentMood === 'sweat') {
                drawHeadPart('prop_sweat');
            } else if (this.currentMood === 'glasses') {
                drawHeadPart('prop_glasses');
            }

            this.ctx.restore(); // End Head Group
            this.ctx.restore(); // End Remix Scale Group
        }

        /**
         * 渲染經典名偵探雪莉 V7
         */
        renderClassicModel() {
            this.time += 0.04;
            const now = Date.now();

            this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);

            this.currentLookX += (this.targetLookX - this.currentLookX) * 0.08;
            this.currentLookY += (this.targetLookY - this.currentLookY) * 0.08;

            const hairPhys = this.hairSpring.update(this.currentLookX * 4.0, this.currentLookY * 3.0);
            const backHairPhys = this.backHairSpring.update(this.currentLookX * 2.5, this.currentLookY * 2.0);

            const breathing = Math.sin(this.time * 2.0) * 2.0;

            if (this.images['hair_back']) {
                this.ctx.save();
                const backPivot = [130, 90];
                const backOffsetX = this.currentLookX * 0.015 * 50 + backHairPhys.x;
                const backOffsetY = this.currentLookY * 0.015 * 40;
                this.ctx.translate(backPivot[0] + backOffsetX, backPivot[1] + backOffsetY);
                this.ctx.drawImage(this.images['hair_back'], -backPivot[0], -backPivot[1]);
                this.ctx.restore();
            }

            if (this.images['body']) {
                this.ctx.save();
                const bodyPivot = [130, 250];
                this.ctx.translate(bodyPivot[0] + this.currentLookX * 0.5, bodyPivot[1] + breathing * 0.5);
                this.ctx.drawImage(this.images['body'], -bodyPivot[0], -bodyPivot[1]);
                this.ctx.restore();
            }

            if (this.images['hand_glass']) {
                this.ctx.save();
                const handPivot = [85, 155];
                let handRot = Math.sin(this.time * 1.5) * 0.04;
                if (this.currentMood === 'detective') {
                    handRot += Math.sin(this.time * 3.0) * 0.08 - 0.1;
                }
                this.ctx.translate(handPivot[0], handPivot[1] + breathing * 0.6);
                this.ctx.rotate(handRot);
                this.ctx.drawImage(this.images['hand_glass'], -handPivot[0], -handPivot[1]);
                this.ctx.restore();
            }

            this.ctx.save();
            const headPivot = [130, 100];
            const headOffsetX = this.currentLookX * 0.04 * 50;
            const headOffsetY = this.currentLookY * 0.04 * 40;
            this.ctx.translate(headPivot[0] + headOffsetX, headPivot[1] + headOffsetY);

            if (this.images['head_base']) {
                this.ctx.drawImage(this.images['head_base'], -headPivot[0], -headPivot[1]);
            }

            if (this.images['blush'] && (this.currentMood === 'blush' || this.currentMood === 'happy')) {
                this.ctx.save();
                this.ctx.globalAlpha = 0.85 + Math.sin(this.time * 4.0) * 0.15;
                this.ctx.drawImage(this.images['blush'], -headPivot[0], -headPivot[1]);
                this.ctx.restore();
            }

            this.ctx.restore();
        }
    }

    // Export globally
    window.SherryLive2DCompanion = SherryLive2DCompanion;

    function bootCompanion() {
        const container = document.getElementById('sherry-live2d-container');
        if (container && !window.sherryCompanion) {
            window.sherryCompanion = new SherryLive2DCompanion('sherry-live2d-container');
            if (window.state && window.state.currentView) {
                window.sherryCompanion.currentView = window.state.currentView;
            }
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', bootCompanion);
    } else {
        bootCompanion();
    }

})(window, document);
