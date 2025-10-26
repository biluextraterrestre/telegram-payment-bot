# --- db_supabase.py (VERSÃO FINAL COMPLETA E CORRIGIDA) ---

import os
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
from supabase import create_client, Client
from telegram import User as TelegramUser

logger = logging.getLogger(__name__)

# --- CONFIGURAÇÃO E CLIENTE SUPABASE ---
url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")
TIMEZONE_BR = timezone(timedelta(hours=-3))

supabase: Client = None
if not url or not key:
    logger.critical("ERRO CRÍTICO: Credenciais do Supabase (URL ou KEY) não encontradas.")
else:
    try:
        supabase: Client = create_client(url, key)
        logger.info("✅ Cliente Supabase criado com sucesso.")
    except Exception as e:
        logger.critical(f"Falha ao criar o cliente Supabase: {e}", exc_info=True)

# --- FUNÇÕES DE USUÁRIO ---

async def get_or_create_user(tg_user: TelegramUser) -> dict | None:
    """Busca ou cria um usuário no banco de dados de forma eficiente usando upsert."""
    if not supabase: return None
    try:
        user_data = {
            "telegram_user_id": tg_user.id,
            "first_name": tg_user.first_name,
            "username": tg_user.username,
        }

        # --- CORREÇÃO APLICADA AQUI ---
        # A chamada .select() foi removida. O upsert já retorna os dados por padrão.
        response = await asyncio.to_thread(
            lambda: supabase.table('users')
            .upsert(user_data, on_conflict='telegram_user_id')
            .execute()
        )
        # --- FIM DA CORREÇÃO ---

        if response.data:
            # Garante que o log de novo usuário seja criado apenas quando necessário
            created_at_str = response.data[0].get('created_at')
            created_at_dt = datetime.fromisoformat(created_at_str)
            if datetime.now(timezone.utc) - created_at_dt < timedelta(seconds=5):
                 await create_log('user_created', f"Novo usuário cadastrado: {tg_user.id}")

            return response.data[0]

        return None
    except Exception as e:
        logger.error(f"❌ [DB] Erro em get_or_create_user para {tg_user.id}: {e}", exc_info=True)
        return None

async def find_user_by_id_or_username(identifier: str) -> dict | None:
    """Busca um usuário pelo seu Telegram ID ou username, incluindo suas assinaturas."""
    if not supabase: return None
    try:
        query = supabase.table('users').select('*, subscriptions(*, product:products(*))')
        if identifier.isdigit():
            query = query.eq('telegram_user_id', int(identifier))
        else:
            username = identifier.lstrip('@')
            query = query.eq('username', username)
        response = await asyncio.to_thread(lambda: query.single().execute())
        return response.data
    except Exception:
        return None

async def find_user_by_db_id(db_id: int) -> dict | None:
    """Busca um usuário pelo seu ID do banco de dados (chave primária)."""
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('users').select('*').eq('id', db_id).single().execute()
        )
        return response.data
    except Exception:
        return None

# --- FUNÇÕES DE PRODUTOS ---

async def get_product_by_id(product_id: int) -> dict | None:
    """Busca os detalhes de um produto pelo seu ID."""
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('products').select('*').eq('id', product_id).single().execute()
        )
        return response.data
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar produto {product_id}: {e}", exc_info=True)
        return None

async def get_all_products() -> List[dict]:
    """Retorna todos os produtos cadastrados, ordenados por preço."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('products').select('*').order('price').execute()
        )
        return response.data or []
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar todos os produtos: {e}", exc_info=True)
        return []

# --- FUNÇÕES DE ASSINATURAS ---

async def create_pending_subscription(
    db_user_id: int, product_id: int, mp_payment_id: str,
    original_price: float, final_price: float, coupon_id: Optional[int] = None,
    external_reference: Optional[str] = None
) -> dict | None:
    """
    Cria uma assinatura pendente, salva a external_reference e registra o uso do cupom.
    """
    if not supabase: return None
    try:
        sub_data = {
            "user_id": db_user_id, "product_id": product_id, "mp_payment_id": mp_payment_id,
            "status": "pending_payment", "original_price": original_price, "final_price": final_price,
            "coupon_id": coupon_id, "external_reference": external_reference
        }

        # --- CORREÇÃO DO ERRO 1 ---
        # A chamada .select() foi removida. O insert já retorna os dados.
        sub_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions').insert(sub_data).execute()
        )
        # --- FIM DA CORREÇÃO ---

        if not sub_response.data:
            logger.error("❌ [DB] Falha ao criar a assinatura pendente no DB (nenhum dado retornado).")
            return None

        new_subscription = sub_response.data[0]
        await create_log('subscription_pending', f"Assinatura pendente {new_subscription['id']} criada para user {db_user_id}")

        if coupon_id:
            await asyncio.to_thread(
                lambda: supabase.table('coupon_usage').insert({
                    "coupon_id": coupon_id,
                    "user_id": db_user_id,
                    "subscription_id": new_subscription['id'],
                    "discount_applied": original_price - final_price
                }).execute()
            )
        return new_subscription
    except Exception as e:
        logger.error(f"❌ [DB] Erro em create_pending_subscription: {e}", exc_info=True)
        return None

async def activate_subscription(mp_payment_id: str) -> dict | None:
    """
    Ativa uma assinatura de forma robusta, sendo tolerante a erros e webhooks duplicados.
    """
    if not supabase: return None
    try:
        # --- CORREÇÃO DO ERRO 2 ---
        # A chamada ao banco de dados está agora dentro do bloco try/except.
        sub_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('*, user:users(*), product:products(*)')
            .eq('mp_payment_id', mp_payment_id)
            .maybe_single()
            .execute()
        )
        # --- FIM DA CORREÇÃO ---

        if not sub_response or not sub_response.data:
            logger.warning(f"⚠️ [DB] Assinatura com mp_payment_id {mp_payment_id} não encontrada no DB.")
            return None

        subscription = sub_response.data

        if subscription.get('status') == 'active':
            logger.info(f"✅ [DB] Assinatura {subscription['id']} já estava ativa. Ignorando re-ativação.")
            return subscription

        user, product = subscription.get('user'), subscription.get('product')
        if not user or not product:
            logger.error(f"❌ [DB] Dados de usuário ou produto ausentes para a assinatura {subscription['id']}.")
            return None

        start_date_base = datetime.now(TIMEZONE_BR)
        active_sub_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions').select('id, end_date').eq('user_id', user['id']).eq('status', 'active').order('end_date', desc=True).limit(1).maybe_single().execute()
        )
        existing_active_sub = active_sub_response.data if active_sub_response else None
        if existing_active_sub and existing_active_sub.get('end_date'):
            old_end_date = datetime.fromisoformat(existing_active_sub['end_date']).astimezone(TIMEZONE_BR)
            if old_end_date > start_date_base:
                start_date_base = old_end_date
                logger.info(f"✅ [DB] Estendendo assinatura para usuário {user['id']}.")

        duration_days = product.get('duration_days')
        new_end_date = start_date_base + timedelta(days=duration_days) if duration_days else None
        update_payload = {
            "status": "active", "start_date": datetime.now(TIMEZONE_BR).isoformat(),
            "end_date": new_end_date.isoformat() if new_end_date else None
        }

        final_response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions').update(update_payload).select('*, user:users(*)').eq('id', subscription['id']).single().execute()
        )
        if final_response.data:
            await create_log('subscription_activated', f"Assinatura {subscription['id']} ativada para usuário {user['telegram_user_id']}")
            logger.info(f"✅ [DB] Assinatura {subscription['id']} ativada com sucesso.")
            final_response.data[0]['product'] = product
            return final_response.data[0]
        else:
            logger.error(f"❌ [DB] Falha ao atualizar e retornar dados da assinatura {subscription['id']}.")
            return None

    except Exception as e:
        logger.error(f"❌ [DB] Erro ao ativar assinatura {mp_payment_id}: {e}", exc_info=True)
        return None

async def get_user_active_subscription(telegram_user_id: int) -> dict | None:
    """Busca a assinatura ativa de um usuário, incluindo dados do produto."""
    if not supabase: return None
    try:
        # A forma mais direta é buscar na tabela de assinaturas e pedir os dados do usuário
        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('*, product:products(*), user:users!inner(*)')
            .eq('user.telegram_user_id', telegram_user_id)
            .eq('status', 'active')
            .limit(1)
            .maybe_single()
            .execute()
        )
        return response.data
    except Exception:
        return None

async def create_manual_subscription(db_user_id: int, product_id: int, admin_notes: str) -> dict | None:
    """Cria uma assinatura ativa manualmente por um admin."""
    if not supabase: return None
    try:
        product = await get_product_by_id(product_id)
        if not product:
            logger.error(f"[DB] Produto {product_id} não encontrado para assinatura manual.")
            return None

        start_date = datetime.now(TIMEZONE_BR)
        end_date = None
        if product.get('duration_days'):
            end_date = start_date + timedelta(days=product['duration_days'])

        insert_data = {
            "user_id": db_user_id,
            "product_id": product_id,
            "mp_payment_id": admin_notes,
            "status": "active",
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat() if end_date else None,
        }

        # --- CORREÇÃO APLICADA AQUI ---
        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions').insert(insert_data).execute()
        )
        # --- FIM DA CORREÇÃO ---

        await create_log('manual_subscription', f"Assinatura manual criada para usuário {db_user_id} - {admin_notes}")
        logger.info(f"✅ [DB] Assinatura manual criada para usuário {db_user_id}.")
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar assinatura manual: {e}", exc_info=True)
        return None

async def revoke_subscription(db_user_id: int, admin_notes: str) -> bool:
    """Revoga a assinatura ativa de um usuário."""
    if not supabase: return False
    try:
        await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .update({"status": "revoked_by_admin", "end_date": datetime.now(TIMEZONE_BR).isoformat()})
            .eq('user_id', db_user_id)
            .eq('status', 'active')
            .execute()
        )
        await create_log('subscription_revoked', f"Assinatura revogada para usuário {db_user_id} - {admin_notes}")
        logger.info(f"✅ [DB] Assinatura do usuário {db_user_id} revogada.")
        return True
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao revogar assinatura: {e}", exc_info=True)
        return False

async def get_all_active_tg_user_ids() -> list[int]:
    """Retorna uma lista de Telegram User IDs de todos os usuários com assinatura ativa."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('subscriptions')
            .select('user:users(telegram_user_id)')
            .eq('status', 'active')
            .execute()
        )
        if not response.data: return []
        # Usa um set para garantir IDs únicos e depois converte para lista
        user_ids = {item['user']['telegram_user_id'] for item in response.data if item.get('user')}
        return list(user_ids)
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar usuários ativos: {e}", exc_info=True)
        return []

# --- FUNÇÕES DE GRUPOS ---

async def get_all_group_ids() -> list[int]:
    """Busca os IDs de todos os grupos cadastrados."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(lambda: supabase.table('groups').select('telegram_chat_id').execute())
        return [item['telegram_chat_id'] for item in response.data] if response.data else []
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar IDs dos grupos: {e}", exc_info=True)
        return []

async def get_all_groups_with_names() -> list[dict]:
    """Busca os IDs e nomes de todos os grupos cadastrados."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(lambda: supabase.table('groups').select('telegram_chat_id, name, created_at').order('name').execute())
        return response.data or []
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar todos os grupos: {e}", exc_info=True)
        return []

async def add_group(chat_id: int, name: str) -> bool:
    """Adiciona um novo grupo ao banco de dados."""
    if not supabase: return False
    try:
        await asyncio.to_thread(
            lambda: supabase.table('groups')
            .upsert({"telegram_chat_id": chat_id, "name": name}, on_conflict='telegram_chat_id')
            .execute()
        )
        logger.info(f"✅ [DB] Grupo {name} ({chat_id}) adicionado/atualizado com sucesso.")
        return True
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao adicionar o grupo {chat_id}: {e}", exc_info=True)
        return False

async def remove_group(chat_id: int) -> bool:
    """Remove um grupo do banco de dados pelo seu telegram_chat_id."""
    if not supabase: return False
    try:
        await asyncio.to_thread(
            lambda: supabase.table('groups')
            .delete()
            .eq('telegram_chat_id', chat_id)
            .execute()
        )
        logger.info(f"✅ [DB] Grupo com chat_id {chat_id} removido com sucesso.")
        return True
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao remover o grupo {chat_id}: {e}", exc_info=True)
        return False

async def get_group_by_chat_id(chat_id: int) -> dict | None:
    """Busca os detalhes de um grupo pelo seu telegram_chat_id."""
    if not supabase: return None
    try:
        response = await asyncio.to_thread(
            lambda: supabase.table('groups').select('*').eq('telegram_chat_id', chat_id).single().execute()
        )
        return response.data
    except Exception:
        return None

# --- FUNÇÕES DE CUPONS ---

async def get_coupon_by_code(code: str, include_inactive: bool = False) -> dict | None:
    """
    Busca um cupom pelo código.
    Por padrão, busca apenas cupons ativos. Se 'include_inactive' for True, busca também os inativos.
    """
    if not supabase: return None
    try:
        query = supabase.table('coupons').select('*').eq('code', code.upper())

        # Adiciona o filtro de 'is_active' apenas se não for para incluir os inativos
        if not include_inactive:
            query = query.eq('is_active', True)

        response = await asyncio.to_thread(lambda: query.single().execute())
        return response.data
    except Exception:
        # Retorna None se o cupom não for encontrado (o que é um comportamento esperado)
        return None

async def create_coupon(
    code: str, discount_type: str, discount_value: float,
    valid_from: Optional[datetime] = None, valid_until: Optional[datetime] = None,
    usage_limit: Optional[int] = None
) -> dict | None:
    """Cria um novo cupom de desconto com todos os campos."""
    if not supabase: return None
    try:
        insert_data = {
            "code": code.upper(), "discount_type": discount_type, "discount_value": discount_value,
            "is_active": True, "usage_limit": usage_limit,
            "valid_from": valid_from.isoformat() if valid_from else None,
            "valid_until": valid_until.isoformat() if valid_until else None
        }

        # --- CORREÇÃO APLICADA AQUI ---
        # O retorno dos dados é solicitado dentro do .execute()
        response = await asyncio.to_thread(
            lambda: supabase.table('coupons').insert(insert_data).execute()
        )
        # --- FIM DA CORREÇÃO ---

        await create_log('coupon_created', f"Cupom criado: {code}")
        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar cupom '{code}': {e}", exc_info=True)
        return None

async def deactivate_coupon(code: str) -> bool:
    """Desativa um cupom."""
    if not supabase: return False
    try:
        await asyncio.to_thread(lambda: supabase.table('coupons').update({"is_active": False}).eq('code', code.upper()).execute())
        await create_log('coupon_deactivated', f"Cupom desativado: {code}")
        return True
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao desativar cupom {code}: {e}", exc_info=True)
        return False

async def reactivate_coupon(code: str) -> bool:
    """Reativa um cupom."""
    if not supabase: return False
    try:
        await asyncio.to_thread(lambda: supabase.table('coupons').update({"is_active": True}).eq('code', code.upper()).execute())
        await create_log('coupon_reactivated', f"Cupom reativado: {code}")
        return True
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao reativar cupom {code}: {e}", exc_info=True)
        return False

async def get_all_coupons(include_inactive: bool = False) -> List[dict]:
    """Busca todos os cupons, opcionalmente incluindo os inativos."""
    if not supabase: return []
    try:
        query = supabase.table('coupons').select('*').order('created_at', desc=True)
        if not include_inactive:
            query = query.eq('is_active', True)
        response = await asyncio.to_thread(lambda: query.execute())
        return response.data or []
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar todos os cupons: {e}", exc_info=True)
        return []

# --- FUNÇÕES DE INDICAÇÃO ---

async def ensure_referral_code_exists(telegram_user_id: int, code: str) -> None:
    """Garante que o código de indicação de um usuário esteja salvo na tabela `users`."""
    if not supabase: return
    try:
        await asyncio.to_thread(
            lambda: supabase.table('users')
            .update({'referral_code': code})
            .eq('telegram_user_id', telegram_user_id)
            .is_('referral_code', 'NULL')
            .execute()
        )
    except Exception as e:
        logger.error(f"❌ [DB] Erro em ensure_referral_code_exists para user {telegram_user_id}: {e}", exc_info=True)

async def find_user_by_referral_code(code: str) -> dict | None:
    """Encontra o usuário dono de um código de referência."""
    if not supabase: return None
    try:
        response = await asyncio.to_thread(lambda: supabase.table('users').select('id, telegram_user_id').eq('referral_code', code.upper()).single().execute())
        return response.data
    except Exception:
        return None

async def create_referral_record(referrer_id: int, referred_id: int, code: str) -> dict | None:
    """Cria um registro na tabela de indicações."""
    if not supabase: return None
    try:
        insert_data = {
            "referrer_id": referrer_id,
            "referred_id": referred_id,
            "referral_code": code.upper()
        }

        # --- CORREÇÃO APLICADA AQUI ---
        response = await asyncio.to_thread(
            lambda: supabase.table('referrals').insert(insert_data).execute()
        )
        # --- FIM DA CORREÇÃO ---

        return response.data[0] if response.data else None
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar registro de indicação: {e}", exc_info=True)
        return None

async def grant_referral_reward(referral_id: int, referrer_id: int) -> bool:
    """Concede a recompensa de 7 dias e marca a indicação como concluída."""
    if not supabase: return False
    try:
        await asyncio.to_thread(lambda: supabase.rpc('extend_subscription_days', {'p_user_id': referrer_id, 'p_days': 7}).execute())
        await asyncio.to_thread(lambda: supabase.table('referrals').update({"reward_granted": True}).eq("id", referral_id).execute())
        logger.info(f"✅ [DB] Recompensa de indicação (ID: {referral_id}) concedida ao usuário {referrer_id}.")
        return True
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao conceder recompensa de indicação {referral_id}: {e}", exc_info=True)
        return False

# --- FUNÇÕES DE LOGS E ESTATÍSTICAS ---

async def create_log(log_type: str, message: str, user_id: Optional[int] = None) -> None:
    """Cria um registro de log no banco de dados."""
    if not supabase: return
    try:
        await asyncio.to_thread(lambda: supabase.table('logs').insert({"type": log_type, "message": message, "user_id": user_id}).execute())
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao criar log: {e}", exc_info=True)

async def get_recent_logs(limit: int = 50) -> List[dict]:
    """Busca os logs mais recentes."""
    if not supabase: return []
    try:
        response = await asyncio.to_thread(lambda: supabase.table('logs').select('*').order('created_at', desc=True).limit(limit).execute())
        return response.data or []
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar logs: {e}", exc_info=True)
        return []

async def get_system_stats() -> Dict[str, Any]:
    """Retorna estatísticas completas do sistema usando funções SQL para eficiência."""
    if not supabase: return {}
    try:
        stats = {}
        # Contagens de usuários, grupos, cupons
        users_resp = await asyncio.to_thread(lambda: supabase.table('users').select('id', count='exact').execute())
        stats['total_users'] = users_resp.count
        groups_resp = await asyncio.to_thread(lambda: supabase.table('groups').select('id', count='exact').execute())
        stats['total_groups'] = groups_resp.count
        coupons_resp = await asyncio.to_thread(lambda: supabase.table('coupons').select('id', count='exact').eq('is_active', True).execute())
        stats['active_coupons'] = coupons_resp.count

        # Contagens de assinaturas por status via RPC
        subs_counts = await asyncio.to_thread(lambda: supabase.rpc('count_subscriptions_by_status').execute())
        if subs_counts.data:
            for item in subs_counts.data:
                # Ajusta os nomes para corresponder ao que o front-end espera
                if item['status'] == 'pending_payment':
                    stats['pending_subscriptions'] = item['count']
                else:
                    stats[f"{item['status']}_subscriptions"] = item['count']
        stats.setdefault('active_subscriptions', 0)
        stats.setdefault('pending_subscriptions', 0)
        stats.setdefault('expired_subscriptions', 0)

        # Contagens de receita via RPC
        revenue_stats = await asyncio.to_thread(lambda: supabase.rpc('get_revenue_stats').execute())
        if revenue_stats.data:
            stats.update(revenue_stats.data[0])
        stats.setdefault('total_revenue', 0)
        stats.setdefault('monthly_revenue', 0)
        stats.setdefault('daily_revenue', 0)

        total_paying_users = stats['active_subscriptions'] + stats['expired_subscriptions']
        stats['conversion_rate'] = (total_paying_users / stats['total_users'] * 100) if stats['total_users'] > 0 else 0
        return stats
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar estatísticas do sistema: {e}", exc_info=True)
        return {}

async def get_referral_stats() -> dict:
    """Busca estatísticas do sistema de indicação para o painel de admin."""
    if not supabase: return {}
    try:
        response = await asyncio.to_thread(lambda: supabase.rpc('get_referral_dashboard_stats').execute())
        return response.data[0] if response.data else {}
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar estatísticas de indicação: {e}", exc_info=True)
        return {}

async def search_transactions(search_term: str) -> List[dict]:
    """Busca transações por diversos critérios."""
    if not supabase: return []
    try:
        query = supabase.table('subscriptions')
        if search_term == 'hoje':
            today = datetime.now(TIMEZONE_BR).date().isoformat()
            query = query.select('*, user:users(*), product:products(*)').gte('created_at', today)
        elif search_term == 'semana':
            week_ago = (datetime.now(TIMEZONE_BR) - timedelta(days=7)).isoformat()
            query = query.select('*, user:users(*), product:products(*)').gte('created_at', week_ago)
        elif search_term.isdigit():
            query = query.select('*, user:users!inner(*), product:products(*)').eq('users.telegram_user_id', int(search_term))
        elif search_term.startswith('@'):
            username = search_term.lstrip('@')
            query = query.select('*, user:users!inner(*), product:products(*)').eq('users.username', username)
        else:
            query = query.select('*, user:users(*), product:products(*)').ilike('mp_payment_id', f'%{search_term}%')
        response = await asyncio.to_thread(lambda: query.order('created_at', desc=True).execute())
        return response.data or []
    except Exception as e:
        logger.error(f"❌ [DB] Erro ao buscar transações com termo '{search_term}': {e}", exc_info=True)
        return []
