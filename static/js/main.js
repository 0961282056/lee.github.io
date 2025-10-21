$(document).ready(function () {
    $("select").select2();
    let shareList = [];
    let pressTimer;

    function copyToClipboard(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text);
        } else {
            const textarea = document.createElement('textarea');
            textarea.value = text;
            document.body.appendChild(textarea);
            textarea.select();
            document.execCommand('copy');
            document.body.removeChild(textarea);
            return Promise.resolve();
        }
    }

    // 長按/滑鼠按下複製邏輯
    $('.card-title').on('touchstart mousedown', function (e) {
        e.preventDefault();
        const $this = $(this);
        const animeName = $this.data('anime-name');
        pressTimer = setTimeout(function () {
            $this.addClass('long-pressed');
            copyToClipboard(animeName).then(() => {
                Swal.fire({ title: '已複製', text: `${animeName} 已複製到剪貼簿！`, icon: 'success', timer: 1500, showConfirmButton: false });
                $this.removeClass('long-pressed');
            }).catch(err => {
                Swal.fire({ title: '失敗', text: '複製失敗，請稍後再試！', icon: 'error', confirmButtonText: '確定' });
                console.error('複製失敗：', err);
            });
        }, 800);
    }).on('touchend touchcancel mouseup mouseleave', function () {
        clearTimeout(pressTimer);
        $(this).removeClass('long-pressed');
    });

    // 點擊彈窗複製
    $('.card-title').on('click', function () {
        const animeName = $(this).data('anime-name');
        Swal.fire({
            title: '操作選擇',
            text: `您想複製 "${animeName}" 嗎？`,
            showCancelButton: true,
            confirmButtonText: '複製',
            cancelButtonText: '取消'
        }).then((result) => {
            if (result.isConfirmed) {
                copyToClipboard(animeName).then(() => {
                    Swal.fire({ title: '已複製', text: `${animeName} 已複製到剪貼簿！`, icon: 'success', timer: 1500, showConfirmButton: false });
                }).catch(err => {
                    Swal.fire({ title: '失敗', text: '複製失敗，請稍後再試！', icon: 'error', confirmButtonText: '確定' });
                });
            }
        });
    });

    // 加入分享清單
    $(".add-to-sharelist").click(function () {
        const card = $(this).closest('.card');
        const anime = {
            name: card.find('.card-title').text(),
            image: card.find('img').attr('src'),
            premiere_date: card.find('.card-text').contents()[0].textContent.replace('首播日期：', '').trim(),
            premiere_time: card.find('.card-text').contents()[2].textContent.replace('首播時間：', '').trim()
        };
        shareList.push(anime);
        updateShareList();
        Swal.fire({ title: '成功', text: '已加入分享清單！', icon: 'success', timer: 1200, showConfirmButton: false });
    });

    function updateShareList() {
        const container = $('#shareList').empty();
        if (shareList.length > 0) {
            shareList.forEach(anime => {
                container.append(`
                    <div class="share-card">
                        <div class="card mb-4">
                            <img src="${anime.image}" class="card-img-top" alt="${anime.name}">
                            <div class="card-body">
                                <h5 class="card-title" data-anime-name="${anime.name}">${anime.name}</h5>
                            </div>
                        </div>
                    </div>`);
            });
            $('#copyButton').show();
        } else {
            $('#copyButton').hide();
        }
    }

    // 複製分享清單為圖片
    $('#copyButton').click(function () {
        if (shareList.length === 0) {
            return Swal.fire({ title: '無內容', text: '分享清單為空，請先添加動畫！', icon: 'warning', confirmButtonText: '確定' });
        }
        html2canvas(document.getElementById('shareList'), { scale: 2 }).then(canvas => {
            canvas.toBlob(blob => {
                navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
                    .then(() => Swal.fire({ title: '已複製', text: '分享清單已作為圖片複製到剪貼簿！', icon: 'success', timer: 1500, showConfirmButton: false }))
                    .catch(err => {
                        Swal.fire({ title: '失敗', text: '複製圖片失敗，請稍後再試！', icon: 'error', confirmButtonText: '確定' });
                        console.error('圖片複製失敗：', err);
                    });
            });
        }).catch(err => {
            Swal.fire({ title: '失敗', text: '生成圖片失敗，請聯繫管理員！', icon: 'error', confirmButtonText: '確定' });
            console.error('html2canvas 錯誤：', err);
        });
        shareList.length = 0;
        updateShareList();
    });
});