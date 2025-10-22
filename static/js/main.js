$(document).ready(function () {
    // 初始化 Select2
    $("select").select2({
        width: '100%',
        placeholder: "選擇...",
        allowClear: true
    });

    let shareList = [];
    let pressTimer;

    // 通用複製文字函數
    async function copyToClipboard(text) {
        try {
            if (navigator.clipboard && navigator.clipboard.writeText) {
                await navigator.clipboard.writeText(text);
            } else {
                // Fallback for older browsers
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.focus();
                textarea.select();
                document.execCommand('copy');
                document.body.removeChild(textarea);
            }
            return true;
        } catch (err) {
            console.error('複製失敗：', err);
            return false;
        }
    }

    // 顯示 SweetAlert 訊息
    function showAlert(title, text, icon = 'info', timer = null, showConfirm = true) {
        const config = {
            title: title,
            text: text,
            icon: icon,
            confirmButtonText: '確定'
        };
        if (timer) {
            config.timer = timer;
            config.showConfirmButton = false;
        }
        if (!showConfirm) {
            config.showConfirmButton = false;
        }
        Swal.fire(config);
    }

    // 長按故事大綱顯示完整內容彈跳視窗（唯一保留的展開方式）
    $('.anime-card').on('touchstart mousedown', '.story-summary', function (e) {
        e.preventDefault();
        const $this = $(this);
        const fullText = $this.text().trim();  // 直接用 .text() 獲取完整內容（移除 title 依賴）
        const animeName = $this.closest('.anime-card').find('.anime-title').data('anime-name') || $this.closest('.anime-card').find('.anime-title').text().trim();
        
        pressTimer = setTimeout(() => {
            $this.addClass('long-pressed');  // 視覺反饋（需在 CSS 新增樣式）
            
            // 使用 SweetAlert 顯示完整故事
            Swal.fire({
                title: `${animeName} - 故事大綱`,
                html: `<div style="text-align: left; white-space: pre-wrap; font-size: 0.9rem; line-height: 1.4;">${fullText}</div>`,
                icon: 'info',
                width: '500px',
                padding: '2rem',
                showConfirmButton: true,
                confirmButtonText: '關閉',
                confirmButtonColor: '#007bff',
                allowOutsideClick: true,
                allowEscapeKey: true
            }).then(() => {
                $this.removeClass('long-pressed');
            });
        }, 800); // 長按延遲 800ms
    }).on('touchend touchcancel mouseup mouseleave', '.story-summary', function () {
        clearTimeout(pressTimer);
        $(this).removeClass('long-pressed');
    });

    // 長按/滑鼠按下複製動畫名稱
    $('.anime-card').on('touchstart mousedown', '.anime-title', function (e) {
        e.preventDefault();
        const $this = $(this);
        const animeName = $this.data('anime-name') || $this.text().trim();
        
        pressTimer = setTimeout(async () => {
            $this.addClass('long-pressed');
            const success = await copyToClipboard(animeName);
            if (success) {
                showAlert('已複製', `${animeName} 已複製到剪貼簿！`, 'success', 1500);
            } else {
                showAlert('失敗', '複製失敗，請稍後再試！', 'error');
            }
            $this.removeClass('long-pressed');
        }, 800); // 長按延遲 800ms
    }).on('touchend touchcancel mouseup mouseleave', '.anime-title', function () {
        clearTimeout(pressTimer);
        $(this).removeClass('long-pressed');
    });

    // 點擊動畫標題彈窗複製
    $('.anime-card').on('click', '.anime-title', function (e) {
        e.stopPropagation(); // 避免長按觸發
        const $this = $(this);
        const animeName = $this.data('anime-name') || $this.text().trim();
        
        Swal.fire({
            title: '複製動畫名稱',
            text: `複製 "${animeName}"？`,
            icon: 'question',
            showCancelButton: true,
            confirmButtonText: '複製',
            cancelButtonText: '取消',
            confirmButtonColor: '#28a745'
        }).then(async (result) => {
            if (result.isConfirmed) {
                const success = await copyToClipboard(animeName);
                if (success) {
                    showAlert('已複製', `${animeName} 已複製到剪貼簿！`, 'success', 1500);
                } else {
                    showAlert('失敗', '複製失敗，請稍後再試！', 'error');
                }
            }
        });
    });

    // 加入分享清單
    $('.anime-card').on('click', '.add-to-sharelist', function (e) {
        e.preventDefault();
        const $card = $(this).closest('.anime-card');
        const anime = {
            name: $card.find('.anime-title').text().trim(),
            image: $card.find('img').attr('src'),
            premiere_date: $card.find('.info-section small').first().text().replace('首播日期：', '').trim(),
            premiere_time: $card.find('.info-section small').eq(1).text().replace('首播時間：', '').trim(),
            story: $card.find('.story-summary').text().trim()  // 直接用 .text() 獲取完整故事（移除 title 依賴）
        };

        // 避免重複加入
        if (!shareList.some(item => item.name === anime.name)) {
            shareList.push(anime);
            updateShareList();
            showAlert('成功', `${anime.name} 已加入分享清單！`, 'success', 1200);
        } else {
            showAlert('已存在', '此動畫已在清單中！', 'info', 1500);
        }
    });

    // 更新分享清單 UI（移除 title，避免原生提示）
    function updateShareList() {
        const $container = $('#shareList').empty();
        if (shareList.length > 0) {
            shareList.forEach((anime, index) => {
                const $shareCard = $(`
                    <div class="share-card row g-3 mb-3">
                        <div class="col-md-4">
                            <img src="${anime.image}" class="img-fluid rounded share-img" alt="${anime.name}" style="width: 300px; height: 300px; object-fit: contain;" loading="lazy">
                        </div>
                        <div class="col-md-8 share-content">
                            <h6 class="anime-name">${anime.name}</h6>
                            <div class="share-info">
                                <small class="text-muted d-block">首播日期：${anime.premiere_date}</small>
                                <small class="text-muted d-block">首播時間：${anime.premiere_time}</small>
                            </div>
                            <div class="share-story mt-2">
                                <small class="text-muted">${anime.story.substring(0, 100)}${anime.story.length > 100 ? '...' : ''}</small>  <!-- 移除 title -->
                            </div>
                            <button class="btn btn-outline-danger btn-sm remove-from-list mt-2" data-index="${index}">移除</button>
                        </div>
                    </div>
                `);
                $container.append($shareCard);
            });
            $('#copyButton').fadeIn(300).prop('disabled', false).text('📋');
        } else {
            $container.html('<p class="text-muted text-center py-4">分享清單為空，點擊「加入分享清單」添加動畫。</p>');
            $('#copyButton').fadeOut(300).prop('disabled', true);
        }
    }

    // 移除分享項目
    $(document).on('click', '.remove-from-list', function () {
        const index = parseInt($(this).data('index'));
        shareList.splice(index, 1);
        updateShareList();
        showAlert('已移除', '動畫已從清單移除！', 'info', 1200);
    });

    // 複製分享清單為圖片（核心功能：轉圖片 + 複製）
    $('#copyButton').click(async function () {
        if (shareList.length === 0) {
            return showAlert('無內容', '分享清單為空，請先添加動畫！', 'warning');
        }

        const $button = $(this).prop('disabled', true).html('<span class="spinner-border spinner-border-sm me-2"></span>生成中...');
        try {
            // 步驟 1: 等待所有圖片載入（解決無圖片問題）
            console.log('開始等待圖片載入...');
            const imagePromises = shareList.map((anime, index) => {
                return new Promise((resolve, reject) => {
                    if (anime.image && anime.image !== '無圖片' && anime.image.startsWith('http')) {
                        const img = new Image();
                        img.crossOrigin = 'anonymous'; // 嘗試跨域
                        img.onload = () => {
                            console.log(`圖片 ${index + 1}/${shareList.length} 載入成功: ${anime.name}`);
                            resolve();
                        };
                        img.onerror = (err) => {
                            console.warn(`圖片 ${index + 1}/${shareList.length} 載入失敗: ${anime.name}`, err);
                            // 即使失敗也 resolve，避免卡住
                            resolve();
                        };
                        img.src = anime.image;
                    } else {
                        console.log(`跳過無效圖片 ${index + 1}/${shareList.length}: ${anime.name}`);
                        resolve();
                    }
                });
            });
            await Promise.all(imagePromises);
            console.log('所有圖片載入完成');

            // 步驟 2: 生成 canvas（優化 scale 和背景）
            console.log('開始生成 canvas...');
            const canvas = await html2canvas(document.getElementById('shareList'), {
                scale: window.devicePixelRatio > 1 ? 2 : 1, // 自適應高 DPI 螢幕
                useCORS: true,  // 允許跨域資源
                allowTaint: true,  // 允許 tainted canvas（即使有跨域問題）
                backgroundColor: '#ffffff',  // 白色背景，避免透明
                width: document.getElementById('shareList').scrollWidth,
                height: document.getElementById('shareList').scrollHeight,
                logging: true  // 開啟 log 除錯
            });
            console.log('Canvas 生成完成，尺寸:', canvas.width, 'x', canvas.height);

            // 步驟 3: 轉 Blob 並複製到剪貼簿
            canvas.toBlob(async (blob) => {
                if (!blob) {
                    throw new Error('Blob 生成失敗');
                }
                console.log('Blob 生成完成，大小:', blob.size, 'bytes');

                try {
                    // 現代瀏覽器：直接寫入剪貼簿
                    await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })]);
                    console.log('圖片成功複製到剪貼簿');
                    showAlert('已複製', `分享清單（${shareList.length} 項）已作為圖片複製！可直接貼上。`, 'success', 2000);
                    shareList = [];  // 清空清單
                    updateShareList();
                } catch (clipboardErr) {
                    console.warn('剪貼簿 API 失敗:', clipboardErr);
                    // Fallback 1: 下載 PNG
                    const link = document.createElement('a');
                    link.download = `anime-share-list-${Date.now()}.png`;
                    link.href = canvas.toDataURL('image/png');
                    document.body.appendChild(link);
                    link.click();
                    document.body.removeChild(link);
                    showAlert('已下載', '圖片已下載到裝置（複製失敗時的備份）！', 'info', 2000);

                    // Fallback 2: 同時複製文字清單
                    const textList = shareList.map(anime => `${anime.name}\n首播：${anime.premiere_date} ${anime.premiere_time}\n故事：${anime.story}`).join('\n\n');
                    await copyToClipboard(textList);
                    console.log('文字清單已備份複製');
                }
            }, 'image/png', 0.95); // 高品質 PNG

        } catch (err) {
            console.error('html2canvas 生成錯誤：', err);
            showAlert('生成失敗', '無法生成圖片，請檢查圖片來源或瀏覽器設定（試試 Chrome）。', 'error');

            // 最終 Fallback: 複製純文字清單
            const textList = shareList.map(anime => `• ${anime.name}\n  首播：${anime.premiere_date} ${anime.premiere_time}\n  故事：${anime.story}`).join('\n\n');
            const success = await copyToClipboard(textList);
            if (success) {
                showAlert('文字備份', `已複製文字清單（${shareList.length} 項）到剪貼簿！`, 'info', 2000);
            }
        } finally {
            $button.prop('disabled', false).html('📋');
        }
    });
});