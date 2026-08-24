<?php
/**
 * Členská brána pro /novinky — Marketingové novinky od Včeliště
 *
 * Vložit jako PHP snippet (Code Snippets / WPCode, "Run everywhere").
 * Statické soubory webu nahrává GitHub Actions přes FTP do:
 *   wp-content/uploads/novinky-data/   (přímý přístup blokuje .htaccess)
 * Tato brána je servíruje pod /novinky/... jen členům kurzu.
 */

const VCELISTE_NOVINKY_COURSE_ID = 6178; // kurz „Marketingové novinky"

add_action('init', function () {
    add_rewrite_rule('^novinky(?:/(.*))?$', 'index.php?vceliste_novinky_path=$matches[1]', 'top');
    // jednorázový flush pravidel po nasazení/změně snippetu (zvyš číslo verze při úpravě)
    if (get_option('vceliste_novinky_rewrite_v') !== '1') {
        flush_rewrite_rules(false);
        update_option('vceliste_novinky_rewrite_v', '1');
    }
});

add_filter('query_vars', function ($vars) {
    $vars[] = 'vceliste_novinky_path';
    return $vars;
});

add_action('template_redirect', function () {
    global $wp;
    if (!array_key_exists('vceliste_novinky_path', $wp->query_vars)) {
        return; // netýká se /novinky
    }

    // Přístup: člen kurzu Marketingové novinky, nebo administrátor.
    // Kdokoli bez přístupu (i nepřihlášený) → stránka kurzu s nabídkou členství.
    $has_access = false;
    if (is_user_logged_in()) {
        $has_access = current_user_can('manage_options')
            || (function_exists('sfwd_lms_has_access')
                && sfwd_lms_has_access(VCELISTE_NOVINKY_COURSE_ID, get_current_user_id()));
    }
    if (!$has_access) {
        $course = get_permalink(VCELISTE_NOVINKY_COURSE_ID);
        wp_safe_redirect($course ?: wp_login_url(home_url('/novinky/')));
        exit;
    }

    // Bezpečné složení cesty k souboru
    $rel = (string) $wp->query_vars['vceliste_novinky_path'];
    if ($rel === '' || substr($rel, -1) === '/') {
        $rel .= 'index.html';
    }
    $rel  = str_replace(chr(0), '', $rel);
    $base = realpath(WP_CONTENT_DIR . '/uploads/novinky-data');
    $file = $base ? realpath($base . '/' . $rel) : false;
    if (!$file || strpos($file, $base) !== 0 || !is_file($file)) {
        status_header(404);
        nocache_headers();
        wp_die('Stránka nenalezena. <a href="' . esc_url(home_url('/novinky/')) . '">Zpět na novinky</a>',
               'Nenalezeno', ['response' => 404]);
    }

    $types = [
        'html' => 'text/html; charset=utf-8', 'css' => 'text/css', 'js' => 'application/javascript',
        'json' => 'application/json', 'xml' => 'application/xml; charset=utf-8',
        'jpg' => 'image/jpeg', 'jpeg' => 'image/jpeg', 'png' => 'image/png',
        'svg' => 'image/svg+xml', 'ico' => 'image/x-icon', 'webp' => 'image/webp',
    ];
    $ext = strtolower(pathinfo($file, PATHINFO_EXTENSION));
    header('Content-Type: ' . ($types[$ext] ?? 'application/octet-stream'));
    header('Content-Length: ' . filesize($file));
    header('Cache-Control: private, max-age=300'); // členský obsah necachovat veřejně
    header('X-Robots-Tag: noindex');
    readfile($file);
    exit;
});
